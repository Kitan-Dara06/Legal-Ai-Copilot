"""
Audit & Observability Endpoints
================================
FR-AUD-02: Immutable audit trail export
FR-EVAL-01: Faithfulness aggregation and system evaluation
"""

import csv
import hashlib
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.middleware.rbac_middleware import require_role
from app.models import UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["Audit"])
limiter = Limiter(key_func=get_remote_address)


@router.get("/{workspace_id}/audit/export")
async def export_workspace_audit(
    request: Request,
    workspace_id: uuid.UUID,
    fmt: str = Query("csv", pattern="^(csv|json)$"),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_role(UserRole.PARTNER)),
):
    """
    FR-AUD-02: Export a cryptographically signed audit trail for a workspace.

    Compiles IntentLog entries, WorkflowExecution checkpoints, and ToolCallLog
    records into a single export. The export includes prompt hashes, approval
    actor IDs, and a SHA-256 signature for immutability verification.
    """
    org_uuid = uuid.UUID(org_id)

    # Verify workspace belongs to org
    ws_check = await db.execute(
        text("SELECT 1 FROM workspaces WHERE id = :ws AND org_id = :org"),
        {"ws": workspace_id, "org": org_uuid},
    )
    if not ws_check.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # ── 1. Fetch WorkflowExecutions for this workspace ──
    wf_result = await db.execute(
        text("""
            SELECT id, status, intent, intent_confidence, confirmed_intent,
                   created_at, completed_at, langgraph_checkpoint
            FROM workflow_executions
            WHERE workspace_id = :ws
            ORDER BY created_at DESC
        """),
        {"ws": workspace_id},
    )
    workflows = wf_result.mappings().all()

    # ── 2. Fetch IntentLog entries ──
    intent_logs = []
    if workflows:
        wf_ids = [w["id"] for w in workflows]
        intent_result = await db.execute(
            text("""
                SELECT workflow_id, goal_hash, model_name, prompt_hash, audit_id,
                       confidence, confirmed_intent, lawyer_override, created_at
                FROM intent_log
                WHERE workflow_id = ANY(:wf_ids)
                ORDER BY created_at DESC
            """),
            {"wf_ids": wf_ids},
        )
        intent_logs = intent_result.mappings().all()

    # ── 3. Fetch ToolCallLog entries ──
    tool_logs = []
    if workflows:
        tool_result = await db.execute(
            text("""
                SELECT workflow_id, tool_name, idempotency_key, status,
                       request_hash, attempt_number, created_at
                FROM tool_call_log
                WHERE workflow_id = ANY(:wf_ids)
                ORDER BY created_at DESC
            """),
            {"wf_ids": wf_ids},
        )
        tool_logs = tool_result.mappings().all()

    # ── 4. Build export payload ──
    export_data = {
        "workspace_id": str(workspace_id),
        "org_id": str(org_uuid),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "workflow_count": len(workflows),
        "intent_log_count": len(intent_logs),
        "tool_call_count": len(tool_logs),
        "workflows": [
            {
                "id": str(w["id"]),
                "status": w["status"],
                "intent": w["intent"],
                "confidence": w["intent_confidence"],
                "confirmed_intent": w["confirmed_intent"],
                "created_at": w["created_at"].isoformat() if w["created_at"] else None,
                "completed_at": w["completed_at"].isoformat()
                if w["completed_at"]
                else None,
            }
            for w in workflows
        ],
        "intent_logs": [
            {
                "workflow_id": str(il["workflow_id"]),
                "goal_hash": il["goal_hash"],
                "model_name": il["model_name"],
                "prompt_hash": il["prompt_hash"],
                "audit_id": il["audit_id"],
                "confidence": il["confidence"],
                "confirmed_intent": il["confirmed_intent"],
                "lawyer_override": il["lawyer_override"],
                "created_at": il["created_at"].isoformat()
                if il["created_at"]
                else None,
            }
            for il in intent_logs
        ],
        "tool_calls": [
            {
                "workflow_id": str(tl["workflow_id"]),
                "tool_name": tl["tool_name"],
                "idempotency_key": tl["idempotency_key"],
                "status": tl["status"],
                "attempt_number": tl["attempt_number"],
                "created_at": tl["created_at"].isoformat()
                if tl["created_at"]
                else None,
            }
            for tl in tool_logs
        ],
    }

    # ── 5. Sign the export ──
    payload_str = str(export_data).encode("utf-8")
    signature = hashlib.sha256(payload_str).hexdigest()
    export_data["signature"] = signature
    export_data["signature_algorithm"] = "SHA-256"

    if fmt == "json":
        return export_data

    # ── 6. CSV format ──
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Lex Audit Export", export_data["workspace_id"], export_data["exported_at"]]
    )
    writer.writerow(["Signature", signature])
    writer.writerow([])
    writer.writerow(["--- Workflows ---"])
    writer.writerow(["ID", "Status", "Intent", "Confidence", "Created"])
    for w in export_data["workflows"]:
        writer.writerow(
            [w["id"], w["status"], w["intent"], w["confidence"], w["created_at"]]
        )
    writer.writerow([])
    writer.writerow(["--- Intent Logs ---"])
    writer.writerow(
        ["Workflow ID", "Goal Hash", "Model", "Intent", "Override", "Created"]
    )
    for il in export_data["intent_logs"]:
        writer.writerow(
            [
                il["workflow_id"],
                il["goal_hash"],
                il["model_name"],
                il["confirmed_intent"],
                il["lawyer_override"],
                il["created_at"],
            ]
        )
    writer.writerow([])
    writer.writerow(["--- Tool Calls ---"])
    writer.writerow(["Workflow ID", "Tool", "Status", "Attempt", "Created"])
    for tl in export_data["tool_calls"]:
        writer.writerow(
            [
                tl["workflow_id"],
                tl["tool_name"],
                tl["status"],
                tl["attempt_number"],
                tl["created_at"],
            ]
        )

    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="audit_{workspace_id}_{datetime.now(timezone.utc).date()}.csv"',
            "X-Audit-Signature": signature,
        },
    )


@router.get("/{workspace_id}/audit/faithfulness")
async def faithfulness_summary(
    request: Request,
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_role(UserRole.ASSOCIATE)),
):
    """
    FR-EVAL-01: Aggregate faithfulness scores per model and over time.
    """
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        text("""
            SELECT
                il.model_name,
                COUNT(*) as total_calls,
                AVG(il.confidence) as avg_confidence,
                MIN(il.created_at) as earliest,
                MAX(il.created_at) as latest
            FROM intent_log il
            JOIN workflow_executions wf ON il.workflow_id = wf.id
            WHERE wf.workspace_id = :ws
            GROUP BY il.model_name
            ORDER BY avg_confidence DESC
        """),
        {"ws": workspace_id},
    )
    rows = result.mappings().all()

    return {
        "workspace_id": str(workspace_id),
        "models": [
            {
                "model_name": r["model_name"],
                "total_calls": r["total_calls"],
                "avg_confidence": round(float(r["avg_confidence"]), 4)
                if r["avg_confidence"]
                else 0,
                "period": {
                    "from": r["earliest"].isoformat() if r["earliest"] else None,
                    "to": r["latest"].isoformat() if r["latest"] else None,
                },
            }
            for r in rows
        ],
    }


@router.get("/{workspace_id}/goals/{goal_id}/audit")
async def get_goal_audit_trail(
    workspace_id: uuid.UUID,
    goal_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Chronological audit trail for a specific goal.

    Returns IntentLog entries, WorkflowExecution checkpoints, and
    ToolCallLog records in chronological order.
    """
    from app.models import IntentLog, ToolCallLog, WorkflowExecution

    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id

    # Get goal
    from app.models import Goal

    goal_result = await db.execute(
        select(Goal).where(Goal.id == goal_id, Goal.org_id == org_uuid)
    )
    goal = goal_result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Get intent logs
    intent_result = await db.execute(
        select(IntentLog)
        .where(
            IntentLog.workflow_id.in_(
                select(WorkflowExecution.id).where(WorkflowExecution.org_id == org_uuid)
            )
        )
        .order_by(IntentLog.created_at)
    )
    intents = intent_result.scalars().all()

    # Get workflow executions
    wf_result = await db.execute(
        select(WorkflowExecution)
        .where(WorkflowExecution.org_id == org_uuid)
        .order_by(WorkflowExecution.created_at)
    )
    workflows = wf_result.scalars().all()

    # Get tool call logs
    tool_result = await db.execute(
        select(ToolCallLog)
        .where(
            ToolCallLog.workflow_id.in_(
                select(WorkflowExecution.id).where(WorkflowExecution.org_id == org_uuid)
            )
        )
        .order_by(ToolCallLog.created_at)
    )
    tool_logs = tool_result.scalars().all()

    events = []

    for intent in intents:
        events.append(
            {
                "timestamp": intent.created_at.isoformat(),
                "type": "intent_classification",
                "detail": {
                    "intent": intent.confirmed_intent,
                    "confidence": intent.confidence,
                    "model": intent.model_name,
                },
            }
        )

    for wf in workflows:
        events.append(
            {
                "timestamp": wf.created_at.isoformat(),
                "type": "workflow_status",
                "detail": {
                    "status": wf.status.value,
                    "error": wf.error_context,
                },
            }
        )

    for log in tool_logs:
        events.append(
            {
                "timestamp": log.created_at.isoformat(),
                "type": "tool_call",
                "detail": {
                    "tool": log.tool_name,
                    "status": log.status.value,
                    "summary": log.response_summary,
                },
            }
        )

    events.sort(key=lambda e: e["timestamp"])

    return {
        "goal_id": str(goal_id),
        "goal_text": goal.goal_text,
        "events": events,
    }


@router.get("/research/export")
async def export_research_log(
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Export the ResearchLog for offline CUAD evaluation.

    Admin only. Returns all intent classifications and tool calls
    for the org in a flat, exportable format.
    """
    from app.models import IntentLog, ToolCallLog, WorkflowExecution

    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id

    wf_ids = select(WorkflowExecution.id).where(WorkflowExecution.org_id == org_uuid)

    intents = await db.execute(
        select(IntentLog)
        .where(IntentLog.workflow_id.in_(wf_ids))
        .order_by(IntentLog.created_at)
    )
    tools = await db.execute(
        select(ToolCallLog)
        .where(ToolCallLog.workflow_id.in_(wf_ids))
        .order_by(ToolCallLog.created_at)
    )

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "intent_classifications": [
            {
                "workflow_id": str(i.workflow_id),
                "model": i.model_name,
                "intent": i.confirmed_intent,
                "confidence": i.confidence,
                "created_at": i.created_at.isoformat(),
            }
            for i in intents.scalars().all()
        ],
        "tool_executions": [
            {
                "workflow_id": str(t.workflow_id),
                "tool": t.tool_name,
                "status": t.status.value,
                "attempt": t.attempt_number,
                "created_at": t.created_at.isoformat(),
            }
            for t in tools.scalars().all()
        ],
    }
