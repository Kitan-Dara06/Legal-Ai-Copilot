"""Escalations Router — Admin intervention for halted workflows.

Section 8.4 SRS:
  GET  /escalations              — List unresolved escalations
  POST /escalations/{id}/resolve — Resolve an escalation (admin only)
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import (
    AuditLog,
    EscalationType,
    WorkflowExecution,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/escalations", tags=["Escalations"])


class EscalationOut(BaseModel):
    id: str
    workflow_id: str
    escalation_type: str
    description: str
    status: str
    created_at: str

    model_config = {"from_attributes": True}


@router.get("", response_model=List[EscalationOut])
async def list_escalations(
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """List unresolved escalations for the org."""
    result = await db.execute(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.org_id == uuid.UUID(org_id),
            WorkflowExecution.status == WorkflowStatus.FAILED,
        )
        .order_by(WorkflowExecution.created_at.desc())
        .limit(50)
    )
    workflows = result.scalars().all()
    return [
        EscalationOut(
            id=str(wf.id),
            workflow_id=str(wf.id),
            escalation_type=wf.error_context.split(":")[0]
            if wf.error_context
            else "UNKNOWN",
            description=wf.error_context or "No error context available",
            status=wf.status.value,
            created_at=wf.created_at.isoformat(),
        )
        for wf in workflows
    ]


@router.post("/{escalation_id}/resolve", status_code=200)
async def resolve_escalation(
    escalation_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a failed workflow escalation (admin)."""
    result = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == escalation_id,
            WorkflowExecution.org_id == uuid.UUID(org_id),
        )
    )
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Escalation not found")

    wf.status = WorkflowStatus.COMPLETED
    wf.error_context = f"Resolved by admin at {datetime.now(timezone.utc).isoformat()}"
    await db.commit()
    return {"status": "resolved", "workflow_id": str(escalation_id)}
