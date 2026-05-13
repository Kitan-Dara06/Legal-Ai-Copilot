"""
Notification Service — Lightweight facade for Slack & Email notifications.

This module provides simple, standalone notification functions used to
alert approvers when actions are awaiting human review. It wraps the
existing notification infrastructure (Slack webhooks, Resend API) with
consistent error handling — failures are logged and never crash the caller.

Environment variables:
    SLACK_WEBHOOK_URL   — Default Slack webhook for approval notifications
    RESEND_API_KEY      — Resend API key for email notifications
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


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


def notify_approval_needed(
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
        logger.warning(
            "[notif] No SLACK_WEBHOOK_URL or RESEND_API_KEY configured — "
            "skipping approval notification for workflow %s",
            workflow_id,
        )
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
        results["slack"] = send_slack_notification(
            webhook_url=slack_url,
            message=slack_message,
            blocks=blocks,
        )

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
            body_parts.append(f"Draft Preview:\n{draft_preview[:1000]}\n")
        if deadline_line:
            body_parts.append(deadline_line.replace("*", ""))
        body_parts.append(f"\n---\nThis notification was sent by Lex Legal AI.")

        results["email"] = send_email_notification(
            to_email=email, subject=subject, body="\n".join(body_parts)
        )
    elif has_resend_key and not email:
        logger.info(
            "[notif] RESEND_API_KEY is set but no approver email available "
            "(checked APPROVER_EMAIL env var and approver_email parameter) — "
            "skipping email for workflow %s",
            workflow_id,
        )

    return results
