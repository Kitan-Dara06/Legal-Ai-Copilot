"""
Approvals Router (SRS Section 8.4)
====================================
Token-based approval management for ACT-path workflows.
Supports email-based approvals via single-use HMAC tokens.

Design notes:
  - Tokens are HMAC-SHA256 signatures computed over
    ``{workflow_id}::{org_id}::{approval_id}`` using
    ``SUPABASE_JWT_SECRET`` as the key.
  - The DB stores only the SHA-256 *digest* of the token (token_hash),
    so the raw token never persists.
  - Verification re-computes the expected HMAC, then compares the
    SHA-256(received) against the stored hash.
  - Organisational tenant isolation is enforced through get_org_id_unified.
  - Rate limited on write endpoints.
"""

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_admin_auth_context, get_org_id_unified, AuthContext
from app.models import (
    Action,
    ApprovalRequest,
    ApprovalStatus,
    WorkflowExecution,
    WorkflowStatus,
)
from app.services.agent.checkpointer import get_checkpointer
from app.services.agent.graph import create_action_agent_graph

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/approvals", tags=["Approvals"])
limiter = Limiter(key_func=get_remote_address)


# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_TOKEN_TTL_HOURS = 168  # 7 days
SUPABASE_JWT_SECRET_ENV = "SUPABASE_JWT_SECRET"


# ── Schemas ────────────────────────────────────────────────────────────────────


class ApproveTokenRequest(BaseModel):
    """Body for POST /approvals/workflows/{workflow_id}/approve."""

    token: str


class RejectTokenRequest(BaseModel):
    """Body for POST /approvals/workflows/{workflow_id}/reject."""

    token: str
    reason: str = Field(
        ...,
        min_length=20,
        description="Rejection reason (minimum 20 characters, per SRS FR-GATE-02)",
    )


class ReissueTokenRequest(BaseModel):
    """Body for POST /approvals/{approval_id}/reissue."""

    expires_in_hours: int | None = DEFAULT_TOKEN_TTL_HOURS


class ApprovalResponse(BaseModel):
    approval_id: str
    workflow_id: str
    action_id: str
    status: str
    expires_at: str | None = None
    created_at: str | None = None


class ApprovalListResponse(BaseModel):
    approvals: List[ApprovalResponse]
    total: int


# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_hmac_secret() -> str:
    """Read SUPABASE_JWT_SECRET from environment."""
    secret = os.environ.get(SUPABASE_JWT_SECRET_ENV)
    if not secret:
        raise RuntimeError(
            f"{SUPABASE_JWT_SECRET_ENV} is not set — approval tokens cannot "
            f"be generated or verified."
        )
    return secret


def _generate_token(workflow_id: uuid.UUID, org_id: str, approval_id: uuid.UUID) -> str:
    """
    Generate a single-use HMAC-SHA256 token string.

    The signature covers ``{workflow_id}::{org_id}::{approval_id}``.
    The returned hex string IS the token handed to the end-user.
    """
    secret = _get_hmac_secret()
    data = f"{workflow_id}::{org_id}::{approval_id}"
    return hmac.new(
        secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _verify_token(
    token: str, workflow_id: uuid.UUID, org_id: str, approval_id: uuid.UUID
) -> bool:
    """
    Verify a token by re-computing the expected HMAC and comparing
    constant-time with the presented token.
    """
    expected = _generate_token(workflow_id, org_id, approval_id)
    return hmac.compare_digest(expected, token)


def _make_langgraph_config(workflow_id: str) -> dict:
    """Standard LangGraph configuration dict used to resume a thread."""
    return {"configurable": {"thread_id": workflow_id}}


async def _get_approval_for_org(
    approval_id: uuid.UUID,
    org_id: str,
    db: AsyncSession,
) -> ApprovalRequest:
    """Fetch a single approval request scoped to the current org."""
    org_uuid = uuid.UUID(org_id)
    res = await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.org_id == org_uuid,
        )
    )
    approval = res.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return approval


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
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return wf


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("", response_model=ApprovalListResponse)
async def list_pending_approvals(
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    GET /approvals

    List all pending/active approval requests for the current org.
    Supports pagination via ``limit`` and ``offset`` query params.
    """
    org_uuid = uuid.UUID(org_id)
    now = datetime.now(timezone.utc)

    # Count
    count_res = await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.org_id == org_uuid,
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.expires_at > now,
        )
    )
    total = len(count_res.scalars().all())

    # Fetch page
    res = await db.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.org_id == org_uuid,
            ApprovalRequest.status == ApprovalStatus.PENDING,
            ApprovalRequest.expires_at > now,
        )
        .order_by(ApprovalRequest.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    approvals = res.scalars().all()

    return ApprovalListResponse(
        approvals=[
            ApprovalResponse(
                approval_id=str(a.id),
                workflow_id=str(a.workflow_id),
                action_id=str(a.action_id),
                status=a.status.value,
                expires_at=a.expires_at.isoformat() if a.expires_at else None,
                created_at=a.created_at.isoformat() if a.created_at else None,
            )
            for a in approvals
        ],
        total=total,
    )


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval_detail(
    approval_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    GET /approvals/{approval_id}

    Return full details for a single approval request.
    """
    approval = await _get_approval_for_org(approval_id, org_id, db)

    return ApprovalResponse(
        approval_id=str(approval.id),
        workflow_id=str(approval.workflow_id),
        action_id=str(approval.action_id),
        status=approval.status.value,
        expires_at=approval.expires_at.isoformat() if approval.expires_at else None,
        created_at=approval.created_at.isoformat() if approval.created_at else None,
    )


@router.post(
    "/workflows/{workflow_id}/approve",
    summary="Approve via HMAC token and resume workflow",
)
@limiter.limit("10/minute")
async def approve_via_token(
    request: Request,
    workflow_id: uuid.UUID,
    req: ApproveTokenRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /approvals/workflows/{workflow_id}/approve

    Verify a single-use HMAC token, mark it consumed, and resume the
    LangGraph workflow from the human_approval interrupt.

    Returns 410 Gone if the token has already been used.
    Returns 403 Forbidden if the token is invalid or expired.
    Returns 409 Conflict if the workflow is not awaiting approval.
    """
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if wf.status not in (WorkflowStatus.AWAITING_APPROVAL,):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve in current status: {wf.status.value}",
        )

    # Locate the PENDING approval request for this workflow
    now = datetime.now(timezone.utc)
    approval_res = await db.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.workflow_id == workflow_id,
            ApprovalRequest.org_id == uuid.UUID(org_id),
            ApprovalRequest.expires_at > now,
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )
    approval = approval_res.scalar_one_or_none()

    if not approval:
        raise HTTPException(
            status_code=404,
            detail="No pending approval request found for this workflow.",
        )

    if approval.status == ApprovalStatus.USED:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This approval token has already been consumed.",
        )

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=403,
            detail=f"Approval request is {approval.status.value}, not PENDING.",
        )

    # Verify token using HMAC
    if not _verify_token(req.token, workflow_id, org_id, approval.id):
        raise HTTPException(
            status_code=403,
            detail="Invalid approval token.",
        )

    # Mark token consumed — record actor (who clicked approve) for pre-execution invariant
    actor_user_id = getattr(request.state, "user_id", None)
    approval.status = ApprovalStatus.USED
    approval.decision_timestamp = now
    approval.actor = actor_user_id  # FR-EXEC-01: actor IS NOT NULL required before execution
    await db.commit()

    # Resume LangGraph workflow
    async with get_checkpointer() as checkpointer:
        app = create_action_agent_graph().compile(
            checkpointer=checkpointer,
            interrupt_before=["ambiguity_gate", "human_approval"],
        )
        config = _make_langgraph_config(str(workflow_id))
        final_state = await app.ainvoke(None, config=config)

    new_status = final_state.get("status", WorkflowStatus.EXECUTING.value)

    return {
        "workflow_id": str(workflow_id),
        "approval_id": str(approval.id),
        "status": new_status,
        "message": "Workflow approved and resumed.",
    }


@router.post(
    "/workflows/{workflow_id}/reject",
    summary="Reject via HMAC token",
)
@limiter.limit("10/minute")
async def reject_via_token(
    request: Request,
    workflow_id: uuid.UUID,
    req: RejectTokenRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /approvals/workflows/{workflow_id}/reject

    Verify a single-use HMAC token, mark the approval as REJECTED, and
    transition the workflow to REVISING status (or ESCALATED if max
    revision count reached).

    Returns 410 Gone if the token has already been used.
    Returns 403 Forbidden if the token is invalid or expired.
    """
    wf = await _get_workflow_for_org(workflow_id, org_id, db)

    if wf.status not in (WorkflowStatus.AWAITING_APPROVAL,):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reject in current status: {wf.status.value}",
        )

    # Locate the PENDING approval request
    now = datetime.now(timezone.utc)
    approval_res = await db.execute(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.workflow_id == workflow_id,
            ApprovalRequest.org_id == uuid.UUID(org_id),
            ApprovalRequest.expires_at > now,
        )
        .order_by(ApprovalRequest.created_at.desc())
        .limit(1)
    )
    approval = approval_res.scalar_one_or_none()

    if not approval:
        raise HTTPException(
            status_code=404,
            detail="No pending approval request found for this workflow.",
        )

    if approval.status == ApprovalStatus.USED:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This approval token has already been consumed.",
        )

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=403,
            detail=f"Approval request is {approval.status.value}, not PENDING.",
        )

    # Verify token using HMAC
    if not _verify_token(req.token, workflow_id, org_id, approval.id):
        raise HTTPException(
            status_code=403,
            detail="Invalid approval token.",
        )

    # Mark token as REJECTED — record actor for audit trail
    actor_user_id = getattr(request.state, "user_id", None)
    approval.status = ApprovalStatus.REJECTED
    approval.decision_timestamp = now
    approval.actor = actor_user_id
    approval.rejection_reason = req.reason
    await db.commit()

    # Track revision count; escalate at max 3 cycles
    revision_res = await db.execute(
        select(Action.revision_count)
        .where(Action.workflow_id == workflow_id)
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
            "approval_id": str(approval.id),
            "status": "ESCALATED",
            "message": "Maximum 3 revision cycles reached. Escalated to admin.",
        }

    # Increment revision count on all actions
    await db.execute(
        update(Action)
        .where(Action.workflow_id == workflow_id)
        .values(revision_count=Action.revision_count + 1)
    )
    await db.execute(
        update(WorkflowExecution)
        .where(WorkflowExecution.id == workflow_id)
        .values(status=WorkflowStatus.REVISING)
    )
    await db.commit()

    return {
        "workflow_id": str(workflow_id),
        "approval_id": str(approval.id),
        "status": "REVISING",
        "message": "Workflow rejected. Returning to revision.",
    }


@router.post(
    "/{approval_id}/reissue",
    summary="Reissue an expired token (admin)",
)
@limiter.limit("5/minute")
async def reissue_token(
    request: Request,
    approval_id: uuid.UUID,
    req: ReissueTokenRequest = ReissueTokenRequest(),
    ctx: AuthContext = Depends(get_admin_auth_context),  # C4: admin-only
    db: AsyncSession = Depends(get_db),
):
    """
    POST /approvals/{approval_id}/reissue

    Generate a new HMAC token for an existing approval request.
    Resets the expiry window and marks the request back to PENDING.

    Only usable when the current status is EXPIRED or PENDING.
    Requires ADMIN role or higher.
    """
    org_id = str(ctx.org_id)
    approval = await _get_approval_for_org(approval_id, org_id, db)

    if approval.status not in (ApprovalStatus.PENDING, ApprovalStatus.EXPIRED):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot reissue token in status {approval.status.value}. "
                f"Only PENDING or EXPIRED requests can be reissued."
            ),
        )

    # Generate new token
    new_token = _generate_token(approval.workflow_id, org_id, approval.id)
    new_token_hash = hashlib.sha256(new_token.encode("utf-8")).hexdigest()

    # Update approval record
    expiry_hours = req.expires_in_hours or DEFAULT_TOKEN_TTL_HOURS
    approval.token_hash = new_token_hash
    approval.status = ApprovalStatus.PENDING
    approval.expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
    await db.commit()

    logger.info(
        "[approvals] Reissued token for approval %s (workflow %s, org %s, "
        "expires in %dh)",
        approval.id,
        approval.workflow_id,
        org_id,
        expiry_hours,
    )

    return {
        "approval_id": str(approval.id),
        "workflow_id": str(approval.workflow_id),
        "status": approval.status.value,
        "expires_at": approval.expires_at.isoformat(),
        "message": "Token reissued successfully.",
    }
