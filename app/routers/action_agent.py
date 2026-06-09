"""
Action Agent Router
====================
POST /agent/start               — Initialize a new LangGraph thread, classify intent
POST /agent/confirm-intent/{id} — Resume thread after ambiguity gate (HITL pause)
POST /agent/confirm-brief/{id}  — Resume thread after decision_brief (HITL pause 1, ACT)
POST /agent/approve/{id}        — Resume thread after draft review (HITL pause 2, ACT)
POST /agent/reject/{id}         — Reject draft with feedback
GET  /agent/status/{id}         — Poll human-readable status
GET  /agent/{id}/actions        — Fetch ordered Action records
GET  /agent/{id}/brief          — Fetch the DecisionBriefResult for this workflow

Design notes:
  - `thread_id` is the workflow UUID (str) — LangGraph identifies the checkpoint by this.
  - interrupt_before=["ambiguity_gate", "decision_brief", "draft"] creates three HITL pauses.
  - The brief payload lives in workflow_executions.decision_brief_payload (JSONB).

Security:
  - All endpoints require Supabase JWT (via get_org_id_unified).
  - Every DB query includes org_id == authenticated_org_id filter.
  - Rate limiting applied to all write endpoints.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import (
    Action,
    Goal,
    IntentLog,
    ToolCallLog,
    WorkflowExecution,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["Action Agent"])
limiter = Limiter(key_func=get_remote_address)


# ── Request schemas ────────────────────────────────────────────────────────────


class StartWorkflowRequest(BaseModel):
    goal_id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID  # Primary document being analyzed


class ConfirmIntentRequest(BaseModel):
    confirmed_intent: str


class ConfirmBriefRequest(BaseModel):
    proceed: bool = True  # False = lawyer aborts after reviewing brief
    override_notes: str | None = None  # Optional lawyer annotation


class ApproveRequest(BaseModel):
    updated_draft: str | None = None  # Live-edited draft from the frontend
    resolved_missing: dict | None = None  # Optional filled-in missing_info values


class RejectRequest(BaseModel):
    reason: str


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_config(workflow_id: str) -> dict:
    return {"configurable": {"thread_id": workflow_id}}


async def _get_workflow_for_org(
    workflow_id: uuid.UUID,
    org_id: str,
    db: AsyncSession,
) -> WorkflowExecution:
    """Fetch a workflow and enforce tenant isolation."""
    org_uuid = uuid.UUID(org_id)
    res = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.id == workflow_id,
            WorkflowExecution.org_id == org_uuid,
        )
    )
    wf = res.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post(
    "/confirm-brief/{workflow_id}",
    summary="Confirm Decision Brief and proceed to drafting",
)
@limiter.limit("10/minute")
async def confirm_brief(
    request: Request,
    workflow_id: uuid.UUID,
    req: ConfirmBriefRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    HITL Pause 1 (ACT path): Lawyer has reviewed the Decision Brief.

    If proceed=True: inject brief_confirmed=True into state and resume.
    If proceed=False: cancel the workflow.
    """
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if wf.status != WorkflowStatus.AWAITING_BRIEF_CONFIRMATION:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm brief in status: {wf.status.value}",
        )

    if not req.proceed:
        # Lawyer chose to abort — mark cancelled
        wf.status = WorkflowStatus.CANCELLED
        await db.commit()
        return {
            "workflow_id": str(workflow_id),
            "status": "CANCELLED",
            "message": "Brief rejected. Workflow cancelled.",
        }

    # ── LangGraph removed — dispatch process_workflow directly ──────────────
    # Keep status as AWAITING_BRIEF_CONFIRMATION so the task knows
    # it is a resume-after-brief and should jump straight to draft_node.
    await db.commit()

    from app.celery_app import celery_app as _celery

    _celery.send_task(
        "app.tasks.process_workflow",
        args=[str(workflow_id)],
        queue="default",
    )
    logger.info(
        "[%s] confirm_brief: dispatched process_workflow for drafting", workflow_id
    )

    return {
        "workflow_id": str(workflow_id),
        "status": "DRAFTING",
        "message": "Brief confirmed. Draft generation started.",
    }


@router.post("/approve/{workflow_id}", summary="Approve draft and trigger export")
@limiter.limit("10/minute")
async def approve_workflow(
    request: Request,
    workflow_id: uuid.UUID,
    req: ApproveRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    HITL Pause 2 (ACT path): Lawyer has reviewed the draft and approves export.

    JWT auth only — no HMAC tokens in the new pipeline.
    Resumes the graph which runs export_node to generate and upload the DOCX.
    """
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if wf.status != WorkflowStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409, detail=f"Cannot approve in status: {wf.status.value}"
        )

    # ── Persist lawyer edits before dispatching export ─────────────────────
    if req.updated_draft or req.resolved_missing:
        from app.models import Action, ActionStatus

        res = await db.execute(
            select(Action)
            .where(
                Action.workflow_id == workflow_id,
                Action.status == ActionStatus.AWAITING_APPROVAL,
            )
            .order_by(Action.task_order)
            .limit(1)
        )
        action = res.scalar_one_or_none()
        if action and action.draft_payload:
            updated_payload = dict(action.draft_payload)
            if req.updated_draft:
                updated_payload["draft_text"] = req.updated_draft
            if req.resolved_missing:
                # Append resolved missing-info answers as a supplementary section
                resolved_notes = "\n\n--- SUPPLEMENTARY INFORMATION ---\n"
                for field, value in req.resolved_missing.items():
                    resolved_notes += f"{field}: {value}\n"
                updated_payload["draft_text"] = (
                    updated_payload.get("draft_text", "") + resolved_notes
                )
                # Clear out the items that were resolved
                remaining_missing = [
                    item for item in updated_payload.get("missing_info", [])
                    if item not in req.resolved_missing
                ]
                updated_payload["missing_info"] = remaining_missing
            action.draft_payload = updated_payload
            # flag_modified tells SQLAlchemy the JSONB column is dirty even
            # when we assign a new dict reference (reference-equality check).
            flag_modified(action, "draft_payload")
            logger.info(
                "[%s] approve_workflow: persisted %s lawyer edits",
                workflow_id,
                "draft_text+missing_info" if req.resolved_missing else "draft_text",
            )

    # ── LangGraph removed — dispatch process_workflow directly ──────────────
    # Keep status as AWAITING_APPROVAL so the task resumes at export_node.
    # Single commit covers both the edit persist and the status check above.
    await db.commit()

    from app.celery_app import celery_app as _celery

    _celery.send_task(
        "app.tasks.process_workflow",
        args=[str(workflow_id)],
        queue="default",
    )
    logger.info(
        "[%s] approve_workflow: dispatched process_workflow for export", workflow_id
    )

    return {
        "workflow_id": str(workflow_id),
        "status": "EXPORTING",
        "message": "Draft approved. Export started.",
    }


@router.post("/reject/{workflow_id}", summary="Reject plan")
@limiter.limit("10/minute")
async def reject_workflow(
    request: Request,
    workflow_id: uuid.UUID,
    req: RejectRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    import hashlib

    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if not req.reason or len(req.reason.strip()) < 20:
        raise HTTPException(
            status_code=400, detail="Rejection reason must be at least 20 characters."
        )

    # Track revision count (max 3 cycles)
    from sqlalchemy import func as sa_func

    revision_res = await db.execute(
        select(Action.revision_count)
        .where(
            Action.workflow_id == workflow_id,
        )
        .order_by(Action.revision_count.desc())
        .limit(1)
    )
    current_revision = revision_res.scalar_one_or_none() or 0

    if current_revision >= 3:
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == workflow_id)
            .values(status=WorkflowStatus.ESCALATED)
        )
        await db.commit()
        return {
            "workflow_id": str(workflow_id),
            "status": "ESCALATED",
            "message": "Maximum 3 revision cycles reached. Escalated to admin.",
        }

    # Increment revision count on all actions and store rejection reason
    # so draft_node can pick it up when regenerating
    res = await db.execute(
        select(Action)
        .where(Action.workflow_id == workflow_id)
        .order_by(Action.task_order)
        .limit(1)
    )
    primary_action = res.scalar_one_or_none()
    if primary_action and primary_action.draft_payload:
        updated = dict(primary_action.draft_payload)
        updated["rejection_reason"] = req.reason.strip()
        updated["revision_number"] = current_revision + 1
        primary_action.draft_payload = updated
        flag_modified(primary_action, "draft_payload")

    await db.execute(
        update(Action)
        .where(Action.workflow_id == workflow_id)
        .values(revision_count=Action.revision_count + 1)
    )

    await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.id == workflow_id,
            WorkflowExecution.org_id == uuid.UUID(org_id),
        )
        .values(status=WorkflowStatus.REVISING)
    )
    await db.commit()

    # Dispatch process_workflow — it will see REVISING and call draft_node again
    from app.celery_app import celery_app as _celery
    _celery.send_task(
        "app.tasks.process_workflow",
        args=[str(workflow_id)],
        queue="default",
    )
    logger.info("[%s] reject_workflow: dispatched revision cycle", workflow_id)

    return {"workflow_id": str(workflow_id), "status": "REVISING"}


@router.get("/status/{workflow_id}", summary="Poll workflow status")
async def get_workflow_status(
    workflow_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    response = {
        "workflow_id": str(wf.id),
        "status": wf.status.value,
        "created_at": wf.created_at.isoformat(),
        "completed_at": wf.completed_at.isoformat() if wf.completed_at else None,
        "download_url": None,
    }

    # When completed, generate a presigned download URL for the DOCX.
    # Always re-generate fresh (presigned URLs expire in 1 hour — re-signing
    # on every status poll ensures the link is always valid regardless of when
    # the user returns to the page).
    if wf.status == WorkflowStatus.COMPLETED and wf.result_ref:
        try:
            from app.services.object_storage import generate_presigned_download_url

            # result_ref stores the authoritative R2 key set by export_node
            response["download_url"] = generate_presigned_download_url(
                wf.result_ref, expires_in=86400  # 24-hour URL
            )
        except Exception as url_err:
            logger.warning(
                "[%s] Failed to generate download URL from result_ref=%s: %s",
                workflow_id, wf.result_ref, url_err,
            )
            # Fallback: try reconstructing from EXECUTED action
            try:
                from app.models import Action, ActionStatus
                res = await db.execute(
                    select(Action).where(
                        Action.workflow_id == workflow_id,
                        Action.status == ActionStatus.EXECUTED,
                    )
                )
                action = res.scalar_one_or_none()
                if action:
                    r2_key = f"drafts/{workflow_id}/{action.id}.docx"
                    from app.services.object_storage import generate_presigned_download_url
                    response["download_url"] = generate_presigned_download_url(
                        r2_key, expires_in=86400
                    )
            except Exception:
                pass

    return response


@router.get("/{workflow_id}/brief", summary="Get Decision Brief for workflow")
async def get_workflow_brief(
    workflow_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the DecisionBriefResult payload stored after decision_brief_node runs.
    Available once status == AWAITING_BRIEF_CONFIRMATION.
    """
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if not wf.decision_brief_payload:
        raise HTTPException(
            status_code=404,
            detail="Decision brief not yet available. Status: " + wf.status.value,
        )

    return {
        "workflow_id": str(wf.id),
        "status": wf.status.value,
        "brief": wf.decision_brief_payload,
    }


@router.get("/{workflow_id}/actions", summary="Get actions for workflow")
async def get_workflow_actions(
    workflow_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership first
    await _get_workflow_for_org(workflow_id, org_id, db)

    org_uuid = uuid.UUID(org_id)
    res = await db.execute(
        select(Action)
        .where(Action.workflow_id == workflow_id, Action.org_id == org_uuid)
        .order_by(Action.task_order)
    )
    actions = res.scalars().all()
    return {
        "actions": [
            {
                "id": str(a.id),
                "action_type": a.action_type.value,
                "description": a.description,
                "status": a.status.value,
                "urgency": float(a.urgency_score) if a.urgency_score else 0.0,
                "draft_payload": a.draft_payload
                or {},  # includes draft_text, citations, grounding_score
            }
            for a in actions
        ]
    }


@router.get("/{workflow_id}/logs", summary="Get tool execution logs")
async def get_workflow_logs(
    workflow_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    # Verify ownership first
    await _get_workflow_for_org(workflow_id, org_id, db)

    res = await db.execute(
        select(ToolCallLog)
        .where(ToolCallLog.workflow_id == workflow_id)
        .order_by(ToolCallLog.created_at)
    )
    logs = res.scalars().all()
    return {
        "logs": [
            {
                "id": str(l.id),
                "tool_name": l.tool_name,
                "status": l.status.value,
                "summary": l.response_summary,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ]
    }
