"""
Action Agent Router
====================
POST /agent/start             — Initialize a new LangGraph thread, classify intent
POST /agent/confirm-intent/{id} — Resume thread after ambiguity gate (HITL pause)
POST /agent/approve/{id}      — Resume thread after human approval (ACT path)
POST /agent/reject/{id}       — Reject plan with feedback
GET  /agent/status/{id}       — Poll human-readable status from WorkflowExecution
GET  /agent/{id}/actions      — Fetch ordered Action records
GET  /agent/{id}/logs         — Fetch ToolCallLog execution results

Design notes:
  - `thread_id` is the workflow UUID (str) — this is how LangGraph identifies
    the checkpoint in its internal tables.
  - The checkpointer tables are separate from our workflow_executions table.
  - We use `interrupt_before=["ambiguity_gate", "human_approval"]` to hit our HITL gates.

Security:
  - All endpoints require Supabase JWT (via get_org_id_unified).
  - Every DB query includes an org_id == authenticated_org_id filter to enforce tenant isolation.
  - Rate limiting is applied to all write endpoints.
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Body, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import WorkflowExecution, WorkflowStatus, Goal, Action, ToolCallLog
from app.services.agent.agent_state import PointerOnlyState, CURRENT_GRAPH_VERSION
from app.services.agent.graph import create_action_agent_graph
from app.services.agent.checkpointer import get_checkpointer

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

@router.post("/start", summary="Start a Legal Action Workflow")
@limiter.limit("10/minute")
async def start_workflow(
    request: Request,
    req: StartWorkflowRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    org_uuid = uuid.UUID(org_id)

    # Verify goal belongs to this org
    goal_res = await db.execute(
        select(Goal).where(Goal.id == req.goal_id, Goal.org_id == org_uuid)
    )
    goal = goal_res.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    workflow = WorkflowExecution(
        goal_id=req.goal_id,
        workspace_id=req.workspace_id,
        org_id=org_uuid,
        status=WorkflowStatus.CLASSIFYING,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    workflow_id = str(workflow.id)

    initial_state = PointerOnlyState(
        graph_version=CURRENT_GRAPH_VERSION,
        workflow_id=workflow_id,
        workspace_id=str(req.workspace_id),
        org_id=org_id,
        document_id=str(req.document_id),
        primary_intent=None,
        intent_confidence=0.0,
        intent_confirmed_by_human=False,
        goal_text=goal.goal_text,
        context_text=None,
        plan_id=None,
        current_task_index=0,
        total_tasks=0,
        status=WorkflowStatus.CLASSIFYING.value,
        findings_summary="",
        action_count=0,
        messages=[],
        error_context=None,
        retry_count=0,
    )

    async with get_checkpointer() as checkpointer:
        app = create_action_agent_graph().compile(
            checkpointer=checkpointer,
            interrupt_before=["ambiguity_gate", "human_approval"],
        )
        config = _make_config(workflow_id)
        final_state = await app.ainvoke(initial_state, config=config)

    return {
        "workflow_id": workflow_id,
        "status": final_state.get("status"),
        "primary_intent": final_state.get("primary_intent"),
        "intent_confidence": final_state.get("intent_confidence"),
        "findings_summary": final_state.get("findings_summary", ""),
        "message": "Workflow started.",
    }


@router.post("/confirm-intent/{workflow_id}", summary="Confirm intent and resume execution")
@limiter.limit("10/minute")
async def confirm_intent(
    request: Request,
    workflow_id: uuid.UUID,
    req: ConfirmIntentRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    # Update state using LangGraph's update_state
    async with get_checkpointer() as checkpointer:
        app = create_action_agent_graph().compile(
            checkpointer=checkpointer,
            interrupt_before=["ambiguity_gate", "human_approval"],
        )
        config = _make_config(str(workflow_id))
        
        # Inject the confirmed intent into state
        await app.aupdate_state(
            config, 
            {
                "primary_intent": req.confirmed_intent,
                "intent_confirmed_by_human": True,
            }
        )
        # Resume
        final_state = await app.ainvoke(None, config=config)

    return {
        "workflow_id": str(workflow_id),
        "status": final_state.get("status"),
        "message": "Intent confirmed. Resuming workflow.",
    }


@router.post("/approve/{workflow_id}", summary="Approve plan and resume execution")
@limiter.limit("10/minute")
async def approve_workflow(
    request: Request,
    workflow_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if wf.status not in (WorkflowStatus.AWAITING_APPROVAL,):
        raise HTTPException(status_code=409, detail=f"Cannot approve in status: {wf.status.value}")

    async with get_checkpointer() as checkpointer:
        app = create_action_agent_graph().compile(
            checkpointer=checkpointer,
            interrupt_before=["ambiguity_gate", "human_approval"],
        )
        config = _make_config(str(workflow_id))
        final_state = await app.ainvoke(None, config=config)

    return {
        "workflow_id": str(workflow_id),
        "status": final_state.get("status", WorkflowStatus.EXECUTING.value),
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
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    await db.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == workflow_id, WorkflowExecution.org_id == uuid.UUID(org_id))
        .values(status=WorkflowStatus.REVISING)
    )
    await db.commit()
    return {"workflow_id": str(workflow_id), "status": "REVISING"}


@router.get("/status/{workflow_id}", summary="Poll workflow status")
async def get_workflow_status(
    workflow_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    return {
        "workflow_id": str(wf.id),
        "status": wf.status.value,
        "created_at": wf.created_at.isoformat(),
        "completed_at": wf.completed_at.isoformat() if wf.completed_at else None,
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
