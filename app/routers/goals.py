"""
Goals Router (SRS Section 8.3)
================================
Unified entry point for ALL user intents — replaces the fragmented
action_agent, agent_query, query, and due_diligence routers.

All endpoints are scoped under /workspaces/{workspace_id}/goals.
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import (
    Action,
    Goal,
    GoalStatus,
    IntentLog,
    IntentType,
    ToolCallLog,
    WorkflowExecution,
    WorkflowStatus,
)
from app.services.agent.nodes import IntentClassification
from app.services.legal_primitives import search_tool
from app.utils import generate_final_answer, sanitize_goal_text

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/goals",
    tags=["Goals"],
)

limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateGoalRequest(BaseModel):
    """Payload for creating a new goal."""

    goal_text: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="The user's goal / question / instruction.",
    )
    mode: str = Field(
        default="hybrid",
        description="Search strategy: hybrid, concept, or multiquery.",
    )


class CreateGoalResponse(BaseModel):
    """Response returned after creating and processing a goal."""

    goal_id: str = Field(..., description="UUID of the created goal.")
    status: str = Field(..., description="Current goal status.")
    answer: Optional[str] = Field(
        default=None, description="Final answer (only for ANALYZE intents)."
    )
    workflow_id: Optional[str] = Field(
        default=None,
        description="Workflow UUID for async tracking (REASON / ACT intents).",
    )
    intent: Optional[str] = Field(
        default=None, description="Classified intent (ANALYZE / REASON / ACT)."
    )
    intent_confidence: Optional[float] = Field(
        default=None, description="Confidence score of the intent classification."
    )


class GoalSummary(BaseModel):
    """Lightweight goal summary for list endpoints."""

    id: str
    goal_text: str
    status: str
    intent: Optional[str] = None
    mode: Optional[str] = None
    created_at: str


class GoalListResponse(BaseModel):
    """Wrapper for the goal list endpoint."""

    goals: List[GoalSummary]
    total: int


class GoalDetailResponse(BaseModel):
    """Detailed goal view including workflow status."""

    id: str
    goal_text: str
    status: str
    intent: Optional[str] = None
    mode: Optional[str] = None
    answer: Optional[str] = None
    created_at: str
    workflows: List[dict] = Field(
        default_factory=list, description="Associated workflow executions."
    )


class GoalResultResponse(BaseModel):
    """Response containing the goal's result (answer or action plan)."""

    goal_id: str
    status: str
    intent: Optional[str] = None
    answer: Optional[str] = None
    actions: List[dict] = Field(
        default_factory=list,
        description="Action items (present for ACT / REASON intents).",
    )
    logs: List[dict] = Field(
        default_factory=list,
        description="Tool execution logs.",
    )


class ConfirmIntentRequest(BaseModel):
    """Payload for confirming / overriding the classified intent."""

    confirmed_intent: str = Field(
        ...,
        description="Must be one of: ANALYZE, REASON, ACT.",
    )


class ConfirmPlanRequest(BaseModel):
    """Payload for approving an action plan."""

    token: Optional[str] = Field(
        default=None,
        description="Approval token from the HMAC-gated approval link.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_goal_for_org(
    goal_id: uuid.UUID,
    workspace_id: uuid.UUID,
    org_id: str,
    db: AsyncSession,
) -> Goal:
    """Fetch a goal and enforce tenant isolation (org + workspace)."""
    org_uuid = uuid.UUID(org_id)
    res = await db.execute(
        select(Goal).where(
            Goal.id == goal_id,
            Goal.workspace_id == workspace_id,
            Goal.org_id == org_uuid,
        )
    )
    goal = res.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


async def _get_workflow_for_goal(
    goal_id: uuid.UUID,
    org_id: str,
    db: AsyncSession,
) -> Optional[WorkflowExecution]:
    """Fetch the most recent workflow execution for a goal, if any."""
    org_uuid = uuid.UUID(org_id)
    res = await db.execute(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.goal_id == goal_id,
            WorkflowExecution.org_id == org_uuid,
        )
        .order_by(WorkflowExecution.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _classify_intent(goal_text: str, groq_client) -> IntentClassification:
    """
    Classify the user's goal using a structured LLM call.
    Returns an IntentClassification with primary_intent, confidence, and reasoning.
    """
    prompt = (
        "You are a legal intent classifier. Your task is to classify the following "
        "user goal into exactly one of these three categories:\n\n"
        "  - ANALYZE: The user wants information, answers, summaries, or analysis of documents.\n"
        "  - REASON: The user wants cross-document reasoning, comparisons, or contradiction detection.\n"
        "  - ACT: The user wants to take action (draft, file, send, notify, escalate, etc.).\n\n"
        "Respond with a JSON object that has exactly these fields:\n"
        '  "primary_intent": one of "ANALYZE", "REASON", "ACT"\n'
        '  "confidence": a float between 0.0 and 1.0\n'
        '  "reasoning": a one-sentence explanation\n'
        '  "requires_graph": true if cross-document reasoning is needed, false otherwise\n\n'
        f'User goal: "{goal_text}"'
    )

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_model={"type": "json_object"},
            temperature=0,
        )
        import json

        raw = json.loads(response.choices[0].message.content)
        classification = IntentClassification(
            primary_intent=raw.get("primary_intent", "ANALYZE").upper(),
            confidence=min(max(float(raw.get("confidence", 0.5)), 0.0), 1.0),
            reasoning=raw.get("reasoning", ""),
            requires_graph=bool(raw.get("requires_graph", False)),
            requires_action=bool(raw.get("requires_action", False)),
            deadline_sensitive=bool(raw.get("deadline_sensitive", False)),
            urgency_score=min(max(float(raw.get("urgency_score", 0.0)), 0.0), 1.0),
        )
        logger.info(
            "Intent classification: %s (confidence=%.2f, graph=%s, reason=%s)",
            classification.primary_intent,
            classification.confidence,
            classification.requires_graph,
            classification.reasoning,
        )
        return classification
    except Exception as e:
        logger.error("Intent classification failed: %s", e)
        return IntentClassification(
            primary_intent="ANALYZE",
            confidence=0.0,
            reasoning="Fallback: defaulting to ANALYZE after classification error.",
            requires_graph=False,
            requires_action=False,
            deadline_sensitive=False,
            urgency_score=0.0,
        )


async def _log_intent_classification(
    workflow_id: uuid.UUID,
    goal_hash: Optional[str],
    model_name: str,
    prompt_text: str,
    classification: IntentClassification,
    db: AsyncSession,
    lawyer_override: bool = False,
) -> None:
    """NFR-AUD-03: Write an audit log entry for intent classification."""
    try:
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        log_entry = IntentLog(
            workflow_id=workflow_id,
            goal_hash=goal_hash,
            model_name=model_name,
            prompt_hash=prompt_hash,
            audit_id="llm_" + str(workflow_id)[:8],
            confidence=classification.confidence,
            confirmed_intent=classification.primary_intent,
            lawyer_override=lawyer_override,
        )
        db.add(log_entry)
        await db.commit()
    except Exception as e:
        logger.warning("[%s] Intent audit logging failed: %s", workflow_id, e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    summary="Create & execute a goal",
    response_model=CreateGoalResponse,
)
@limiter.limit("30/minute")
async def create_goal(
    request: Request,
    workspace_id: uuid.UUID,
    req: CreateGoalRequest = Body(...),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new goal and immediately begin processing.

    The goal text is sanitised and classified into one of three intents:

    - **ANALYZE** — Information retrieval. Runs `search_tool` + `generate_final_answer`
      and returns the answer synchronously.
    - **REASON** — Cross-document reasoning. Creates a `WorkflowExecution` and returns
      a `workflow_id` for async tracking.
    - **ACT** — Action-oriented. Creates a `WorkflowExecution` and returns a `workflow_id`
      for async tracking.

    Rate-limited to 30 requests per minute.
    """
    org_uuid = uuid.UUID(org_id)

    # --- Sanitise input ---
    safe_text = sanitize_goal_text(req.goal_text)
    goal_hash = hashlib.sha256(safe_text.encode("utf-8")).hexdigest()

    # --- Create Goal row ---
    user_id = getattr(request.state, "user_id", None)
    goal = Goal(
        workspace_id=workspace_id,
        org_id=org_uuid,
        user_id=user_id,
        goal_text=safe_text,
        goal_hash=goal_hash,
        mode=req.mode,
        status=GoalStatus.PENDING,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    goal_id = goal.id

    # --- Classify intent via Groq ---
    groq_client = request.app.state.groq_client
    classification = await _classify_intent(safe_text, groq_client)

    goal.intent = IntentType(classification.primary_intent)
    goal.status = GoalStatus.PROCESSING
    await db.commit()

    # --- Route based on intent ---
    if classification.primary_intent == "ANALYZE":
        # Synchronous ANALYZE path: search + generate answer
        try:
            all_chunks = await run_in_threadpool(
                search_tool,
                query=safe_text,
                mode=req.mode,
                top_k=5,
                org_id=org_id,
            )
            answer = await run_in_threadpool(
                generate_final_answer, safe_text, all_chunks, groq_client
            )
        except Exception as e:
            logger.error("ANALYZE processing failed for goal %s: %s", goal_id, e)
            goal.status = GoalStatus.FAILED
            await db.commit()
            raise HTTPException(
                status_code=500,
                detail="Failed to process ANALYZE goal. Please try again.",
            )

        goal.answer = answer
        goal.status = GoalStatus.COMPLETED
        await db.commit()

        return CreateGoalResponse(
            goal_id=str(goal_id),
            status=GoalStatus.COMPLETED.value,
            answer=answer,
            intent=classification.primary_intent,
            intent_confidence=classification.confidence,
        )

    # --- REASON or ACT path: create WorkflowExecution for async processing ---
    workflow = WorkflowExecution(
        goal_id=goal_id,
        workspace_id=workspace_id,
        org_id=org_uuid,
        intent=IntentType(classification.primary_intent),
        intent_confidence=classification.confidence,
        intent_reasoning=classification.reasoning,
        status=WorkflowStatus.CLASSIFYING,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    workflow_id = workflow.id

    # NFR-AUD-03: Log the intent classification
    await _log_intent_classification(
        workflow_id=workflow_id,
        goal_hash=goal_hash,
        model_name="llama-3.3-70b-versatile",
        prompt_text=safe_text,
        classification=classification,
        db=db,
    )

    return CreateGoalResponse(
        goal_id=str(goal_id),
        status=GoalStatus.PROCESSING.value,
        workflow_id=str(workflow_id),
        intent=classification.primary_intent,
        intent_confidence=classification.confidence,
    )


@router.get(
    "",
    summary="List goals for workspace",
    response_model=GoalListResponse,
)
async def list_goals(
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200, description="Max results."),
    offset: int = Query(default=0, ge=0, description="Pagination offset."),
    status_filter: Optional[str] = Query(
        default=None,
        description="Optional status filter (PENDING, PROCESSING, COMPLETED, etc.).",
    ),
):
    """
    List all goals for the specified workspace, with optional status filtering
    and pagination. Results are ordered by creation date (newest first).
    """
    org_uuid = uuid.UUID(org_id)

    query = (
        select(Goal)
        .where(Goal.workspace_id == workspace_id, Goal.org_id == org_uuid)
        .order_by(Goal.created_at.desc())
    )

    if status_filter:
        try:
            status_enum = GoalStatus(status_filter.upper())
            query = query.where(Goal.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status filter: '{status_filter}'. "
                f"Valid values: {[s.value for s in GoalStatus]}",
            )

    # Get total count
    count_res = await db.execute(
        select(Goal.id)
        .where(Goal.workspace_id == workspace_id, Goal.org_id == org_uuid)
        .order_by(Goal.created_at.desc())
    )
    total = len(count_res.scalars().all())

    # Paginated results
    res = await db.execute(query.offset(offset).limit(limit))
    goals = res.scalars().all()

    return GoalListResponse(
        goals=[
            GoalSummary(
                id=str(g.id),
                goal_text=g.goal_text[:200],
                status=g.status.value if g.status else "UNKNOWN",
                intent=g.intent.value if g.intent else None,
                mode=g.mode,
                created_at=g.created_at.isoformat(),
            )
            for g in goals
        ],
        total=total,
    )


@router.get(
    "/{goal_id}",
    summary="Get goal status and details",
    response_model=GoalDetailResponse,
)
async def get_goal(
    workspace_id: uuid.UUID,
    goal_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve the full details of a goal, including its current status,
    classified intent, answer (if completed), and any associated workflow
    executions.
    """
    goal = await _get_goal_for_org(goal_id, workspace_id, org_id, db)
    workflow = await _get_workflow_for_goal(goal_id, org_id, db)

    workflows_list = []
    if workflow:
        workflows_list.append(
            {
                "id": str(workflow.id),
                "status": workflow.status.value if workflow.status else None,
                "intent": workflow.intent.value if workflow.intent else None,
                "intent_confidence": workflow.intent_confidence,
                "created_at": workflow.created_at.isoformat(),
                "completed_at": workflow.completed_at.isoformat()
                if workflow.completed_at
                else None,
            }
        )

    return GoalDetailResponse(
        id=str(goal.id),
        goal_text=goal.goal_text,
        status=goal.status.value if goal.status else "UNKNOWN",
        intent=goal.intent.value if goal.intent else None,
        mode=goal.mode,
        answer=goal.answer,
        created_at=goal.created_at.isoformat(),
        workflows=workflows_list,
    )


@router.get(
    "/{goal_id}/result",
    summary="Get goal result",
    response_model=GoalResultResponse,
)
async def get_goal_result(
    workspace_id: uuid.UUID,
    goal_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve the final result of a completed goal.

    For **ANALYZE** goals, returns the direct answer.

    For **REASON** and **ACT** goals, returns the action plan with
    tool execution logs.
    """
    goal = await _get_goal_for_org(goal_id, workspace_id, org_id, db)
    workflow = await _get_workflow_for_goal(goal_id, org_id, db)

    actions_list = []
    logs_list = []

    if workflow:
        org_uuid = uuid.UUID(org_id)
        # Fetch actions
        actions_res = await db.execute(
            select(Action)
            .where(
                Action.workflow_id == workflow.id,
                Action.org_id == org_uuid,
            )
            .order_by(Action.task_order)
        )
        for a in actions_res.scalars().all():
            actions_list.append(
                {
                    "id": str(a.id),
                    "action_type": a.action_type.value if a.action_type else None,
                    "description": a.description,
                    "status": a.status.value if a.status else None,
                    "urgency": float(a.urgency_score) if a.urgency_score else 0.0,
                }
            )

        # Fetch tool logs
        logs_res = await db.execute(
            select(ToolCallLog)
            .where(ToolCallLog.workflow_id == workflow.id)
            .order_by(ToolCallLog.created_at)
        )
        for log_row in logs_res.scalars().all():
            logs_list.append(
                {
                    "id": str(log_row.id),
                    "tool_name": log_row.tool_name,
                    "status": log_row.status.value if log_row.status else None,
                    "summary": log_row.response_summary,
                    "created_at": log_row.created_at.isoformat(),
                }
            )

    return GoalResultResponse(
        goal_id=str(goal.id),
        status=goal.status.value if goal.status else "UNKNOWN",
        intent=goal.intent.value if goal.intent else None,
        answer=goal.answer,
        actions=actions_list,
        logs=logs_list,
    )


@router.post(
    "/{goal_id}/confirm-intent",
    status_code=200,
    summary="Confirm or override intent classification",
)
@limiter.limit("10/minute")
async def confirm_intent(
    request: Request,
    workspace_id: uuid.UUID,
    goal_id: uuid.UUID,
    req: ConfirmIntentRequest = Body(...),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Confirm or override the automatically classified intent.

    This acts as an ambiguity gate — when the system's confidence is low,
    the user can explicitly set the intent to ANALYZE, REASON, or ACT.

    The override is recorded in the intent audit log (NFR-AUD-03).
    """
    # Validate confirmed_intent
    confirmed = req.confirmed_intent.upper()
    if confirmed not in ("ANALYZE", "REASON", "ACT"):
        raise HTTPException(
            status_code=400,
            detail="confirmed_intent must be one of: ANALYZE, REASON, ACT.",
        )

    goal = await _get_goal_for_org(goal_id, workspace_id, org_id, db)

    # Update the goal's intent
    goal.intent = IntentType(confirmed)
    await db.commit()

    # If there's an existing workflow, log the override and update it
    workflow = await _get_workflow_for_goal(goal_id, org_id, db)
    if workflow:
        # NFR-AUD-03: Log human override
        prompt_text = f"Human override: {confirmed}"
        override_classification = IntentClassification(
            primary_intent=confirmed,
            confidence=1.0,
            reasoning="Manually confirmed by user.",
            requires_graph=(confirmed == "REASON"),
            requires_action=(confirmed == "ACT"),
            deadline_sensitive=False,
            urgency_score=0.0,
        )
        await _log_intent_classification(
            workflow_id=workflow.id,
            goal_hash=goal.goal_hash,
            model_name="human_override",
            prompt_text=prompt_text,
            classification=override_classification,
            db=db,
            lawyer_override=True,
        )

        # Update workflow intent fields
        workflow.intent = IntentType(confirmed)
        workflow.intent_confirmed_by_human = True
        workflow.confirmed_intent = IntentType(confirmed)
        await db.commit()

    return {
        "goal_id": str(goal_id),
        "confirmed_intent": confirmed,
        "message": f"Intent confirmed as {confirmed}.",
    }


@router.post(
    "/{goal_id}/confirm-plan",
    status_code=200,
    summary="Approve action plan",
)
@limiter.limit("10/minute")
async def confirm_plan(
    request: Request,
    workspace_id: uuid.UUID,
    goal_id: uuid.UUID,
    req: ConfirmPlanRequest = Body(...),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve the action plan for a goal.

    For **ACT** and **REASON** intents, the system generates an action plan
    that requires human approval before execution. This endpoint confirms
    the plan and resumes the workflow.

    If a HMAC-gated `token` is provided, it is verified against the stored
    approval request before resuming.
    """
    goal = await _get_goal_for_org(goal_id, workspace_id, org_id, db)
    workflow = await _get_workflow_for_goal(goal_id, org_id, db)

    if not workflow:
        raise HTTPException(
            status_code=404,
            detail="No workflow found for this goal. "
            "Only goals with REASON or ACT intents have action plans.",
        )

    if workflow.status != WorkflowStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve plan in status: {workflow.status.value}. "
            f"Expected AWAITING_APPROVAL.",
        )

    # If a token was provided, verify it
    if req.token:
        from app.models import ApprovalRequest, ApprovalStatus

        token_hash = hashlib.sha256(req.token.encode()).hexdigest()
        approval_res = await db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.workflow_id == workflow.id,
                ApprovalRequest.token_hash == token_hash,
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at > datetime.now(timezone.utc),
            )
        )
        approval = approval_res.scalar_one_or_none()
        if not approval:
            raise HTTPException(
                status_code=403,
                detail="Invalid, expired, or already used approval token.",
            )
        approval.status = ApprovalStatus.USED
        await db.commit()

    # Resume the LangGraph workflow via the checkpointer
    try:
        from app.services.agent.checkpointer import get_checkpointer
        from app.services.agent.graph import create_action_agent_graph

        async with get_checkpointer() as checkpointer:
            app = create_action_agent_graph().compile(
                checkpointer=checkpointer,
                interrupt_before=["ambiguity_gate", "human_approval"],
            )
            config = {"configurable": {"thread_id": str(workflow.id)}}
            final_state = await app.ainvoke(None, config=config)

        goal.status = GoalStatus.PROCESSING
        await db.commit()

        return {
            "goal_id": str(goal_id),
            "workflow_id": str(workflow.id),
            "status": final_state.get("status", WorkflowStatus.EXECUTING.value),
            "message": "Plan approved. Resuming workflow.",
        }
    except Exception as e:
        logger.error(
            "Failed to resume workflow %s after plan approval: %s",
            workflow.id,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to resume workflow after plan approval.",
        )


@router.delete(
    "/{goal_id}",
    status_code=200,
    summary="Cancel a goal",
)
async def cancel_goal(
    workspace_id: uuid.UUID,
    goal_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel a goal and any associated workflows.

    Sets the goal status to CANCELLED and, if a workflow exists,
    sets the workflow status to CANCELLED as well.
    """
    goal = await _get_goal_for_org(goal_id, workspace_id, org_id, db)

    if goal.status in (GoalStatus.COMPLETED, GoalStatus.CANCELLED):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel goal in status: {goal.status.value}.",
        )

    goal.status = GoalStatus.CANCELLED
    await db.commit()

    # Cancel any active workflows
    org_uuid = uuid.UUID(org_id)
    await db.execute(
        update(WorkflowExecution)
        .where(
            WorkflowExecution.goal_id == goal_id,
            WorkflowExecution.org_id == org_uuid,
            WorkflowExecution.status.notin_(
                [
                    WorkflowStatus.COMPLETED,
                    WorkflowStatus.CANCELLED,
                    WorkflowStatus.FAILED,
                ]
            ),
        )
        .values(
            status=WorkflowStatus.CANCELLED,
            completed_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    return {
        "goal_id": str(goal_id),
        "status": GoalStatus.CANCELLED.value,
        "message": "Goal cancelled.",
    }
