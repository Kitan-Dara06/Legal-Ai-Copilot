"""Notifications Router — In-app notification fallback (FR-NOTIF-01).

Endpoints:
  GET  /notifications          — List unread notifications for the user
  POST /notifications/{id}/read — Mark a notification as read
  POST /notifications/read-all  — Mark all notifications as read

These are ephemeral user notifications, NOT the immutable AuditLog.
Notifications are created when Slack/Email delivery fails after retries.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import Notification

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationOut(BaseModel):
    id: str
    title: str
    body: str
    notification_type: str
    action_url: str | None = None
    is_read: bool
    created_at: str

    model_config = {"from_attributes": True}


@router.get("", response_model=List[NotificationOut])
async def list_notifications(
    request: Request,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """List unread notifications for the authenticated user, newest first."""
    result = await db.execute(
        select(Notification)
        .where(
            Notification.org_id == uuid.UUID(org_id),
            Notification.is_read == False,
        )
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    return [
        NotificationOut(
            id=str(n.id),
            title=n.title,
            body=n.body,
            notification_type=n.notification_type,
            action_url=n.action_url,
            is_read=n.is_read,
            created_at=n.created_at.isoformat(),
        )
        for n in result.scalars().all()
    ]


@router.post("/{notification_id}/read", status_code=200)
async def mark_notification_read(
    notification_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.org_id == uuid.UUID(org_id),
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    notif.is_read = True
    notif.read_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "ok"}


@router.post("/read-all", status_code=200)
async def mark_all_notifications_read(
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Mark all unread notifications as read for this org."""
    await db.execute(
        update(Notification)
        .where(
            Notification.org_id == uuid.UUID(org_id),
            Notification.is_read == False,
        )
        .values(is_read=True, read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"status": "ok"}
