"""
Tool Registry — Centralized registry for all external action tools.

Each tool declares:
  - idempotency_class: IDEMPOTENT | NON_IDEMPOTENT | REQUIRES_COMPENSATION
  - compensation_fn: name of compensation function (or None if non-compensatable)
  - retry_policy: (max_retries, base_delay_s) for exponential backoff
"""

import logging
import os
from enum import Enum
from typing import Any, Callable, Optional

from app.models import ActionType, IdempotencyClass

logger = logging.getLogger(__name__)


class RetryPolicy:
    """Exponential backoff: delay = base_delay * 2^attempt"""

    def __init__(self, max_retries: int = 3, base_delay_s: float = 10.0):
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s


class ToolMetadata:
    def __init__(
        self,
        action_type: ActionType,
        idempotency_class: IdempotencyClass,
        compensation_fn: Optional[str] = None,
        retry_policy: Optional[RetryPolicy] = None,
        description: str = "",
    ):
        self.action_type = action_type
        self.idempotency_class = idempotency_class
        self.compensation_fn = compensation_fn
        self.retry_policy = retry_policy or RetryPolicy()
        self.description = description


# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[ActionType, ToolMetadata] = {
    ActionType.DRAFT_RESPONSE: ToolMetadata(
        action_type=ActionType.DRAFT_RESPONSE,
        idempotency_class=IdempotencyClass.IDEMPOTENT,
        compensation_fn=None,
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=5.0),
        description="Draft a legal response document",
    ),
    ActionType.FILE_DOCUMENT: ToolMetadata(
        action_type=ActionType.FILE_DOCUMENT,
        idempotency_class=IdempotencyClass.IDEMPOTENT,
        compensation_fn="unfile_document",
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=10.0),
        description="File a document with the court or regulatory body",
    ),
    ActionType.SEND_NOTICE: ToolMetadata(
        action_type=ActionType.SEND_NOTICE,
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        compensation_fn=None,  # Cannot unsend an email
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=30.0),
        description="Send a legal notice via email or Slack",
    ),
    ActionType.UPDATE_CASE_TRACKER: ToolMetadata(
        action_type=ActionType.UPDATE_CASE_TRACKER,
        idempotency_class=IdempotencyClass.REQUIRES_COMPENSATION,
        compensation_fn="revert_case_tracker",
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=5.0),
        description="Update case management system (Clio)",
    ),
    ActionType.SET_REMINDER: ToolMetadata(
        action_type=ActionType.SET_REMINDER,
        idempotency_class=IdempotencyClass.REQUIRES_COMPENSATION,
        compensation_fn="delete_reminder",
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=5.0),
        description="Set a calendar reminder for a deadline",
    ),
    ActionType.ESCALATE_TO_COUNSEL: ToolMetadata(
        action_type=ActionType.ESCALATE_TO_COUNSEL,
        idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        compensation_fn=None,  # Cannot undo an escalation
        retry_policy=RetryPolicy(max_retries=3, base_delay_s=30.0),
        description="Escalate to supervising counsel for review",
    ),
}


def get_tool_metadata(action_type: ActionType) -> Optional[ToolMetadata]:
    """Look up a tool's metadata by ActionType."""
    return TOOL_REGISTRY.get(action_type)


# ── External API Connectors ───────────────────────────────────────────────────


async def dispatch_send_notice(task: Any) -> str:
    """Send a legal notice via SMTP email (NON_IDEMPOTENT)."""
    draft = (task.draft_payload or {}).get("draft_text", "")
    recipient = (task.draft_payload or {}).get("recipient", "")
    subject = (task.draft_payload or {}).get("subject", "Legal Notice")

    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_host or not smtp_user:
        logger.warning("[tool] SMTP not configured — logging notice instead of sending")
        logger.info(
            "[tool] WOULD SEND: To=%s Subject=%s Body=%s",
            recipient,
            subject,
            draft[:200],
        )
        return f"NOTICE_LOGGED (SMTP not configured): {subject} to {recipient}"

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(draft, "plain")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    return f"NOTICE_SENT: {subject} to {recipient}"


async def dispatch_update_case_tracker(task: Any) -> str:
    """Update case tracker via Clio API (IDEMPOTENT with compensation)."""
    draft = (task.draft_payload or {}).get("draft_text", "")
    case_id = (task.draft_payload or {}).get("case_id", "")
    description = task.description

    clio_api_key = os.getenv("CLIO_API_KEY", "")

    if not clio_api_key:
        logger.warning("[tool] Clio API not configured — logging instead")
        return f"CASE_LOGGED (Clio not configured): {description[:100]}"

    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://app.clio.com/api/v4/activities.json",
            headers={"Authorization": f"Bearer {clio_api_key}"},
            json={
                "activity": {
                    "description": description,
                    "notes": draft[:5000],
                }
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return f"CASE_UPDATED: activity {data.get('activity', {}).get('id', 'unknown')}"


async def dispatch_set_reminder(task: Any) -> str:
    """Set a calendar reminder via Nylas Events API (IDEMPOTENT with compensation)."""
    draft = (task.draft_payload or {}).get("draft_text", "")
    due_date = (task.draft_payload or {}).get("due_date", "")
    description = task.description
    grant_id = (task.draft_payload or {}).get("grant_id", "")

    nylas_api_key = os.getenv("NYLAS_API_KEY", "")
    nylas_grant_id = grant_id or os.getenv("NYLAS_GRANT_ID", "")

    if not nylas_api_key:
        logger.warning("[tool] Nylas not configured — logging reminder instead")
        return f"REMINDER_LOGGED (Nylas not configured): {description[:100]} due {due_date}"

    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.nylas.com/v3/grants/" + nylas_grant_id + "/events",
                headers={
                    "Authorization": f"Bearer {nylas_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "title": description,
                    "description": draft[:5000] if draft else "",
                    "when": {
                        "date": {"date": due_date},
                    },
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            event_id = data.get("data", {}).get("id", "unknown")
            return f"REMINDER_SET: {description[:100]} due {due_date} (event_id={event_id})"
    except Exception as e:
        logger.warning("[tool] Nylas event creation failed: %s", e)
        return f"REMINDER_LOGGED (Nylas error): {description[:100]} due {due_date} — {str(e)[:100]}"


async def dispatch_escalate_to_counsel(task: Any) -> str:
    """Escalate to supervising counsel via Slack (NON_IDEMPOTENT)."""
    draft = (task.draft_payload or {}).get("draft_text", "")
    channel = (task.draft_payload or {}).get("slack_channel", "#legal-escalations")

    slack_webhook = os.getenv("SLACK_WEBHOOK_URL", "")

    if not slack_webhook:
        logger.warning("[tool] Slack not configured — logging escalation instead")
        return f"ESCALATION_LOGGED (Slack not configured): {draft[:200]}"

    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            slack_webhook,
            json={"text": f"*Legal Escalation Required*\n{draft[:3000]}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        return f"ESCALATED via Slack to {channel}"


# ── Dispatch Router ───────────────────────────────────────────────────────────

DISPATCH_MAP = {
    ActionType.SEND_NOTICE: dispatch_send_notice,
    ActionType.UPDATE_CASE_TRACKER: dispatch_update_case_tracker,
    ActionType.SET_REMINDER: dispatch_set_reminder,
    ActionType.ESCALATE_TO_COUNSEL: dispatch_escalate_to_counsel,
}


async def dispatch_tool(task: Any) -> str:
    """
    Route a task to the appropriate external API connector.
    Falls back to a log-based mock if no connector exists.
    """
    action_type = task.action_type
    draft = (task.draft_payload or {}).get("draft_text", "")

    handler = DISPATCH_MAP.get(action_type)
    if handler:
        try:
            return await handler(task)
        except Exception as e:
            logger.error("[tool] %s failed: %s", action_type.value, e)
            raise

    # Fallback mock for unknown or non-executable action types
    msg = f"Executed {action_type.value}: '{task.description[:100]}'"
    if draft:
        msg += f" | Draft: {draft[:200]}..."
    return msg
