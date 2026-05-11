"""Notification Service — Slack webhooks, Resend email, .ics calendar, dedup.

Lex SRS compliant:
  - Slack: incoming webhook only (no OAuth), NON_IDEMPOTENT
  - Email: Resend for system notifications
  - .ics: calendar attachments for deadline-related notifications
  - Dedup: SHA-256(workflow_id + type + recipient) with 3600s Redis TTL
  - Failure: 3 retries (30s/60s/120s), then log UNCOMPENSATABLE_FAILURE
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("Resend", "").strip()

# ── Constants ────────────────────────────────────────────────────────────────
NOTIF_COOLDOWN_SECONDS = 3600  # 1 hour
MAX_RETRIES = 3
RETRY_DELAYS = [30, 60, 120]


# ── Deduplication ────────────────────────────────────────────────────────────


def _build_dedup_key(workflow_id: str, notif_type: str, recipient_id: str) -> str:
    """Build a Redis key for notification deduplication.

    Format: lex:notif:{sha256(workflow_id + type + recipient_id)}
    This prevents silencing legitimate alerts for different recipients
    or different notification types on the same workflow.
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


# ── Slack Webhook ────────────────────────────────────────────────────────────


def send_slack_webhook(
    webhook_url: str,
    text: str,
    title: Optional[str] = None,
    actions: Optional[list] = None,
    draft_excerpt: Optional[str] = None,
    deadline: Optional[str] = None,
) -> bool:
    """Send a richly formatted message to a Slack incoming webhook.

    FR-GATE-01: Message MUST contain:
      - Action description
      - Draft excerpt (capped at 500 chars)
      - Link to full draft in Lex app
      - HMAC-secured approve/reject URLs
      - Deadline (if applicable)

    NON_IDEMPOTENT: If this fires twice, Slack shows two messages.
    """
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title or "Lex Approval Request"},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
    ]

    if draft_excerpt:
        excerpt = draft_excerpt[:500]
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Draft Excerpt:*\n{excerpt}"},
            }
        )

    if deadline:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"⏰ *Deadline:* {deadline}"}],
            }
        )

    if actions:
        blocks.append({"type": "divider"})
        elements = []
        for action in actions:
            elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": action["label"]},
                    "url": action["url"],
                    "style": action.get("style", "primary"),
                }
            )
        blocks.append(
            {
                "type": "actions",
                "elements": elements[:5],  # Slack max 5 buttons per block
            }
        )

    payload = {
        "text": text,
        "blocks": blocks,
        "unfurl_links": False,
        "unfurl_media": False,
    }

    try:
        response = httpx.post(webhook_url, json=payload, timeout=15.0)
        if response.status_code == 200:
            logger.info("[slack] Notification sent successfully")
            return True
        else:
            logger.error(
                "[slack] Failed with status %s: %s",
                response.status_code,
                response.text[:200],
            )
            return False
    except httpx.TimeoutException:
        logger.error("[slack] Timeout sending notification")
        return False
    except Exception as e:
        logger.error("[slack] Error: %s", e)
        return False


# ── Resend Email ─────────────────────────────────────────────────────────────


def send_email_via_resend(
    to: str,
    subject: str,
    body: str,
    ics_content: Optional[str] = None,
) -> bool:
    """Send an email via Resend API.

    If ics_content is provided, attaches a .ics calendar file.
    This is used for deadline-related notifications and approval requests.

    NON_IDEMPOTENT: If this fires twice, recipient gets two emails.
    """
    if not RESEND_API_KEY:
        logger.error("[email] RESEND_API_KEY not configured")
        return False

    payload = {
        "from": "Lex Legal AI <notifications@legalrag.codes>",
        "to": [to],
        "subject": subject,
        "text": body,
    }

    if ics_content:
        payload["attachments"] = [
            {
                "filename": "event.ics",
                "content": ics_content,
                "content_type": "text/calendar",
            }
        ]

    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        if response.status_code == 200:
            logger.info("[email] Sent to %s: %s", to, subject)
            return True
        else:
            logger.error(
                "[email] Failed (%s): %s", response.status_code, response.text[:200]
            )
            return False
    except httpx.TimeoutException:
        logger.error("[email] Timeout sending to %s", to)
        return False
    except Exception as e:
        logger.error("[email] Error: %s", e)
        return False


# ── .ics Calendar Generator ─────────────────────────────────────────────────


def generate_ics_event(
    summary: str,
    description: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    location: Optional[str] = None,
    url: Optional[str] = None,
) -> str:
    """Generate a .ics calendar file content as a string.

    Used for deadline notifications so lawyers can add events
    to Outlook/Apple Calendar/Google Calendar.
    """
    try:
        from dateutil.tz import tzutc
        from icalendar import Calendar, Event, vCalAddress, vText
    except ImportError:
        logger.warning(
            "[ics] icalendar not installed — install with: pip install icalendar"
        )
        return ""

    cal = Calendar()
    cal.add("prodid", "-//Lex Legal AI//EN")
    cal.add("version", "2.0")

    event = Event()
    event.add("summary", summary)
    event.add("description", description)
    event.add("dtstart", start_time)
    event.add("dtend", end_time or start_time)
    event.add("dtstamp", datetime.now(timezone.utc))

    if location:
        event.add("location", location)
    if url:
        event.add("url", url)

    cal.add_component(event)
    return cal.to_ical().decode("utf-8")


# ── High-Level Dispatcher ────────────────────────────────────────────────────


async def dispatch_notification(
    channel: str,
    recipient: str,
    workflow_id: str,
    notif_type: str,
    message: str,
    **kwargs,
) -> bool:
    """Dispatch a notification with dedup, retry, and failure logging.

    Args:
        channel: "slack" or "email"
        recipient: Slack webhook URL or email address
        workflow_id: Workflow UUID for dedup key
        notif_type: Notification type for dedup key
        message: Notification body text
        **kwargs: Passed to send_slack_webhook or send_email_via_resend

    Returns:
        True if sent successfully, False after exhausting retries
    """
    # 1. Check dedup
    if await check_notification_cooldown(workflow_id, notif_type, recipient):
        logger.info("[notif] Skipping duplicate notification for %s", workflow_id)
        return True  # Not an error — already sent

    # 2. Send with retry
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            if channel == "slack":
                success = send_slack_webhook(recipient, message, **kwargs)
            elif channel == "email":
                success = send_email_via_resend(recipient, message, **kwargs)
            else:
                logger.error("[notif] Unknown channel: %s", channel)
                return False

            if success:
                await mark_notification_sent(workflow_id, notif_type, recipient)
                return True

        except Exception as e:
            last_error = e
            logger.warning(
                "[notif] Attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, e
            )

        if attempt < MAX_RETRIES - 1:
            import asyncio

            delay = RETRY_DELAYS[attempt]
            logger.info("[notif] Retrying in %ds...", delay)
            await asyncio.sleep(delay)

    # 3. All retries exhausted — log UNCOMPENSATABLE_FAILURE
    logger.error(
        "[notif] All %d retries exhausted for %s workflow=%s",
        MAX_RETRIES,
        notif_type,
        workflow_id,
    )
    # TODO: Write UNCOMPENSATABLE_FAILURE to AuditLog via DB session
    return False
