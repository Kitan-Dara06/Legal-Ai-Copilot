"""
Notification Service — Lightweight facade for Slack & Email notifications.

This module provides simple, standalone notification functions used to
alert approvers when actions are awaiting human review. It wraps the
existing notification infrastructure (Slack webhooks, Resend API) with
consistent error handling — failures are logged and never crash the caller.

Features:
  - Redis-based dedup (SHA-256 key, 3600s TTL)
  - Retry logic (3 attempts: 30s/60s/120s)

Environment variables:
    SLACK_WEBHOOK_URL   — Default Slack webhook for approval notifications
    RESEND_API_KEY      — Resend API key for email notifications
"""

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
NOTIF_COOLDOWN_SECONDS = 3600  # 1 hour
MAX_RETRIES = 3
RETRY_DELAYS = [30, 60, 120]


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _retry_send(send_fn, *args, **kwargs) -> bool:
    """Retry a send function up to MAX_RETRIES times with backoff."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            result = send_fn(*args, **kwargs)
            if result:
                return True
        except Exception as e:
            last_error = e
            logger.warning(
                "[notif] Attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, e
            )

        if attempt < MAX_RETRIES - 1:
            import asyncio

            delay = RETRY_DELAYS[attempt]
            await asyncio.sleep(delay)

    if last_error:
        logger.error("[notif] All %d retries exhausted: %s", MAX_RETRIES, last_error)
    return False


# ── Deduplication ────────────────────────────────────────────────────────────


def _build_dedup_key(workflow_id: str, notif_type: str, recipient_id: str) -> str:
    """Build a Redis key for notification deduplication.

    Format: lex:notif:{sha256(workflow_id + type + recipient_id)}
    """
    raw = f"{workflow_id}{notif_type}{recipient_id}"
    key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"lex:notif:{key_hash}"


async def check_notification_cooldown(
    workflow_id: str, notif_type: str, recipient_id: str
) -> bool:
    """Check if this notification was sent in the last hour.

    Returns True if the notification is still on cooldown (should NOT send).
    Returns False if it's safe to send.
    """
    try:
        from app.redis_client import create_redis_pool

        redis = create_redis_pool()
        key = _build_dedup_key(workflow_id, notif_type, recipient_id)
        exists = await redis.exists(key)
        await redis.aclose()
        return bool(exists)
    except Exception as e:
        logger.warning("[notif] Dedup check failed (proceeding): %s", e)
        return False


async def mark_notification_sent(
    workflow_id: str, notif_type: str, recipient_id: str
) -> None:
    """Mark this notification as sent for cooldown tracking."""
    try:
        from app.redis_client import create_redis_pool

        redis = create_redis_pool()
        key = _build_dedup_key(workflow_id, notif_type, recipient_id)
        await redis.setex(key, NOTIF_COOLDOWN_SECONDS, "1")
        await redis.aclose()
    except Exception as e:
        logger.warning("[notif] Failed to mark cooldown: %s", e)


def send_slack_notification(
    webhook_url: str, message: str, blocks: Optional[list] = None
) -> bool:
    """
    Send a Slack message via webhook.

    Args:
        webhook_url: Slack incoming webhook URL.
        message:     Plain-text fallback message.
        blocks:      Optional Slack Block Kit blocks for rich formatting.

    Returns:
        True if the message was accepted by Slack, False otherwise.
    """
    payload: dict = {"text": message}

    if blocks:
        payload["blocks"] = blocks
        payload.setdefault("unfurl_links", False)
        payload.setdefault("unfurl_media", False)

    try:
        response = httpx.post(webhook_url, json=payload, timeout=15.0)
        if response.status_code == 200:
            logger.info("[notif] Slack notification sent successfully")
            return True
        else:
            logger.error(
                "[notif] Slack webhook responded %s: %s",
                response.status_code,
                response.text[:300],
            )
            return False
    except httpx.TimeoutException:
        logger.error("[notif] Slack webhook timed out")
        return False
    except Exception as e:
        logger.error("[notif] Slack notification failed: %s", e)
        return False


def send_email_notification(
    to_email: str,
    subject: str,
    body: str,
    api_key_env: str = "RESEND_API_KEY",
) -> bool:
    """
    Send an email via the Resend API.

    Args:
        to_email:    Recipient email address.
        subject:     Email subject line.
        body:        Plain-text email body.
        api_key_env: Environment variable name holding the Resend API key.

    Returns:
        True if the email was accepted by Resend, False otherwise.
    """
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        logger.error("[notif] Resend API key not found in env var '%s'", api_key_env)
        return False

    payload = {
        "from": "Lex Legal AI <notifications@legalrag.codes>",
        "to": [to_email],
        "subject": subject,
        "text": body,
    }

    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        if response.status_code == 200:
            logger.info("[notif] Email sent to %s: %s", to_email, subject)
            return True
        else:
            logger.error(
                "[notif] Resend responded %s: %s",
                response.status_code,
                response.text[:300],
            )
            return False
    except httpx.TimeoutException:
        logger.error("[notif] Resend API timed out for %s", to_email)
        return False
    except Exception as e:
        logger.error("[notif] Email notification failed: %s", e)
        return False


async def notify_approval_needed(
    workflow_id: str,
    action_summary: str,
    draft_preview: str,
    days_remaining: Optional[int] = None,
    slack_webhook: Optional[str] = None,
    approver_email: Optional[str] = None,
) -> dict:
    """
    Send an approval-request notification through all configured channels.

    Checks environment variables as defaults, and also accepts explicit
    overrides.  The function will never crash — all send failures are
    caught, logged, and reflected in the returned result dict.

    Args:
        workflow_id:    Workflow UUID (used in Slack message text).
        action_summary: Human-readable summary of the action awaiting approval.
        draft_preview:  Excerpt of the drafted document (capped at 500 chars).
        days_remaining: Optional number of days until deadline.
        slack_webhook:  Override the default SLACK_WEBHOOK_URL.
        approver_email: Override the default approver email address.

    Returns:
        Dict with keys ``slack`` and ``email``, each mapping to a bool
        indicating whether that channel's send succeeded.
    """
    results: dict[str, bool] = {"slack": False, "email": False}

    # ── Resolve config ───────────────────────────────────────────────────
    slack_url = slack_webhook or os.getenv("SLACK_WEBHOOK_URL", "").strip()
    has_resend_key = bool(os.getenv("RESEND_API_KEY", "").strip())
    email = approver_email or os.getenv("APPROVER_EMAIL", "").strip()

    if not slack_url and not has_resend_key:
        logger.info(
            "[notif] No SLACK_WEBHOOK_URL or RESEND_API_KEY configured — "
            "skipping approval notification for workflow %s",
            workflow_id,
        )
        return results

    # ── Dedup check ──────────────────────────────────────────────────────
    if slack_url:
        if await check_notification_cooldown(workflow_id, "approval_slack", slack_url):
            logger.info("[notif] Slack notification on cooldown for %s", workflow_id)
            results["slack"] = True  # Already sent, treat as success

    if email:
        if await check_notification_cooldown(workflow_id, "approval_email", email):
            logger.info("[notif] Email notification on cooldown for %s", workflow_id)
            results["email"] = True  # Already sent, treat as success

    if results["slack"] and results["email"]:
        return results

    # ── Build shared content ─────────────────────────────────────────────
    deadline_line = ""
    if days_remaining is not None:
        if days_remaining > 0:
            deadline_line = f"⏰ *Deadline:* {days_remaining} day(s) remaining"
        elif days_remaining == 0:
            deadline_line = "⏰ *Deadline:* Due today"
        else:
            deadline_line = "⏰ *Deadline:* OVERDUE"

    # ── Slack ────────────────────────────────────────────────────────────
    if slack_url:
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📋 Lex — Approval Required",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Workflow:* `{workflow_id}`\n*Action:* {action_summary}"
                    ),
                },
            },
        ]

        if draft_preview:
            excerpt = draft_preview[:500]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Draft Excerpt:*\n{excerpt}",
                    },
                }
            )

        if deadline_line:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": deadline_line}],
                }
            )

        slack_message = f"Approval needed for workflow {workflow_id}: {action_summary}"
        results["slack"] = await _retry_send(
            send_slack_notification,
            webhook_url=slack_url,
            message=slack_message,
            blocks=blocks,
        )
        if results["slack"] and email:
            await mark_notification_sent(workflow_id, "approval_slack", slack_url)

    # ── Email ────────────────────────────────────────────────────────────
    if has_resend_key and email:
        subject = f"Lex — Approval Required: {action_summary[:80]}"
        body_parts = [
            f"Workflow ID: {workflow_id}",
            f"",
            f"Action: {action_summary}",
            f"",
        ]
        if draft_preview:
            # Truncate at a sentence boundary instead of mid-word
            preview = draft_preview[:1200]
            # Find the last sentence-ending punctuation within the limit
            for end_char in [". ", ".\n", "\n\n"]:
                last_pos = preview[:1000].rfind(end_char)
                if last_pos > 200:  # Only use if we keep a meaningful amount
                    preview = preview[: last_pos + 1]
                    break
            else:
                preview = preview[:1000]
            if len(draft_preview) > len(preview):
                preview += "\n\n[Draft truncated — view full draft in the application]"
            body_parts.append(f"Draft Preview:\n{preview}\n")
        if deadline_line:
            body_parts.append(deadline_line.replace("*", ""))
        body_parts.append(f"\n---\nThis notification was sent by Lex Legal AI.")

        results["email"] = await _retry_send(
            send_email_notification,
            to_email=email,
            subject=subject,
            body="\n".join(body_parts),
        )
        if results["email"]:
            await mark_notification_sent(workflow_id, "approval_email", email)
    elif has_resend_key and not email:
        logger.info(
            "[notif] RESEND_API_KEY is set but no approver email available "
            "(checked APPROVER_EMAIL env var and approver_email parameter) — "
            "skipping email for workflow %s",
            workflow_id,
        )

    return results
