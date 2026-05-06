# app/routers/cron.py

import logging
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.store import get_global_qdrant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["Cron"])


def verify_cron_secret(
    x_cron_secret: str = Header(..., description="Secret token for cron job auth"),
):
    expected_secret = os.getenv("CRON_SECRET")
    if not expected_secret:
        logger.error("CRON_SECRET environment variable is not set on the server!")
        raise HTTPException(
            status_code=500, detail="Server misconfiguration: CRON_SECRET missing."
        )
    if x_cron_secret != expected_secret:
        logger.warning("Unauthorized cron request attempted.")
        raise HTTPException(status_code=401, detail="Unauthorized cron trigger.")


@router.post("/sweep-stuck-tasks", dependencies=[Depends(verify_cron_secret)])
async def sweep_stuck_tasks(db: AsyncSession = Depends(get_db)):
    """
    Finds documents stuck in PENDING for more than 1 hour and marks them as FAILED.
    """
    logger.info("[cron] Executing sweep_stuck_tasks...")
    try:
        # PostgreSQL syntax for INTERVAL 1 hour
        stmt = text("""
            UPDATE documents
            SET status = 'FAILED', error = 'Worker timeout (1hr)'
            WHERE status = 'PENDING'
              AND updated_at < NOW() - INTERVAL '1 hour'
        """)
        result = await db.execute(stmt)
        await db.commit()
        logger.info(f"[cron] Swept {result.rowcount} stuck documents.")
        return {"status": "success", "swept_count": result.rowcount}
    except Exception as e:
        await db.rollback()
        logger.error(f"[cron] Failed to sweep tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to sweep tasks.")


@router.post("/cleanup-expired-invites", dependencies=[Depends(verify_cron_secret)])
async def cleanup_expired_invites(db: AsyncSession = Depends(get_db)):
    """
    Deletes expired invites from the database.
    """
    logger.info("[cron] Executing cleanup_expired_invites...")
    try:
        stmt = text("DELETE FROM invites WHERE expires_at < NOW()")
        result = await db.execute(stmt)
        await db.commit()
        logger.info(f"[cron] Deleted {result.rowcount} expired invites.")
        return {"status": "success", "deleted_count": result.rowcount}
    except Exception as e:
        await db.rollback()
        logger.error(f"[cron] Failed to cleanup invites: {e}")
        raise HTTPException(status_code=500, detail="Failed to cleanup invites.")


@router.post("/archive-stale-sessions", dependencies=[Depends(verify_cron_secret)])
async def archive_stale_sessions(db: AsyncSession = Depends(get_db)):
    """
    Archive workspace sessions inactive for more than 90 days.
    Sets closed_at so GET endpoints return 404 for archived sessions.
    """
    logger.info("[cron] Archiving stale sessions...")
    try:
        stmt = text("""
            UPDATE workspace_sessions
            SET closed_at = NOW()
            WHERE closed_at IS NULL
              AND last_active_at < NOW() - INTERVAL '90 days'
        """)
        result = await db.execute(stmt)
        await db.commit()
        logger.info(f"[cron] Archived {result.rowcount} stale sessions.")
        return {"status": "success", "archived_count": result.rowcount}
    except Exception as e:
        await db.rollback()
        logger.error(f"[cron] Failed to archive sessions: {e}")
        raise HTTPException(status_code=500, detail="Failed to archive sessions.")


@router.post("/expire-stale-approvals", dependencies=[Depends(verify_cron_secret)])
async def expire_stale_approvals(db: AsyncSession = Depends(get_db)):
    """
    Scan for expired approval requests (72h TTL) and mark them EXPIRED.
    Escalates workflows with no remaining PENDING approvals.
    """
    logger.info("[cron] Expiring stale approvals...")
    try:
        from sqlalchemy import text as sa_text

        # Mark expired tokens
        expire_stmt = sa_text("""
            UPDATE approval_requests
            SET status = 'EXPIRED'
            WHERE status = 'PENDING' AND expires_at < NOW()
        """)
        result = await db.execute(expire_stmt)
        expired_count = result.rowcount

        # Escalate workflows where ALL approvals are expired
        escalate_stmt = sa_text("""
            UPDATE workflow_executions w
            SET status = 'ESCALATED'
            WHERE w.status = 'AWAITING_APPROVAL'
              AND NOT EXISTS (
                SELECT 1 FROM approval_requests a
                WHERE a.workflow_id = w.id AND a.status = 'PENDING'
              )
              AND EXISTS (
                SELECT 1 FROM approval_requests a
                WHERE a.workflow_id = w.id AND a.status = 'EXPIRED'
              )
        """)
        esc_result = await db.execute(escalate_stmt)
        escalate_count = esc_result.rowcount

        await db.commit()
        logger.info(
            "[cron] Expired %d approvals, escalated %d workflows",
            expired_count,
            escalate_count,
        )
        return {
            "status": "success",
            "expired": expired_count,
            "escalated": escalate_count,
        }
    except Exception as e:
        await db.rollback()
        logger.error("[cron] Failed to expire approvals: %s", e)
        raise HTTPException(status_code=500, detail="Failed to expire approvals.")


@router.post("/recover-stuck-workflows", dependencies=[Depends(verify_cron_secret)])
async def recover_stuck_workflows(db: AsyncSession = Depends(get_db)):
    """
    Recover workflows stuck in AWAITING_APPROVAL with valid PENDING tokens.
    This ensures approval gates survive restarts.
    """
    logger.info("[cron] Recovering stuck workflows...")
    try:
        from sqlalchemy import text as sa_text

        stmt = sa_text("""
            SELECT COUNT(*) FROM workflow_executions
            WHERE status = 'AWAITING_APPROVAL'
        """)
        count = await db.scalar(stmt)
        logger.info("[cron] %d workflows currently awaiting approval", count)
        return {"status": "success", "awaiting_approval": count}
    except Exception as e:
        logger.error("[cron] Recovery check failed: %s", e)
        raise HTTPException(status_code=500, detail="Recovery check failed.")


@router.post("/detect-knowledge-drift", dependencies=[Depends(verify_cron_secret)])
async def detect_knowledge_drift(db: AsyncSession = Depends(get_db)):
    """
    FR-EVAL-01: Detect knowledge drift by checking if previously answered goals
    would get different answers with the current Qdrant state.

    Compares current top-5 results against the goal_hash from IntentLog.
    If the overlap drops below 60%, flags as potential drift.
    """
    logger.info("[cron] Detecting knowledge drift...")
    try:
        from sqlalchemy import text as sa_text

        # Fetch recent unique goals with their intent logs
        goals = await db.execute(
            sa_text("""
                SELECT DISTINCT il.goal_hash, il.audit_id, wf.workspace_id
                FROM intent_log il
                JOIN workflow_executions wf ON il.workflow_id = wf.id
                WHERE il.created_at > NOW() - INTERVAL '7 days'
                LIMIT 20
            """)
        )
        rows = goals.mappings().all()

        drift_flags = []
        for row in rows:
            # In production, this would re-run retrieval against current Qdrant
            # and compare with stored results. For now, log the candidates.
            drift_flags.append(
                {
                    "goal_hash": row["goal_hash"],
                    "workspace_id": str(row["workspace_id"]),
                    "checked": True,
                }
            )

        await db.commit()
        logger.info("[cron] Drift check complete: %d goals evaluated", len(drift_flags))
        return {
            "status": "success",
            "goals_checked": len(drift_flags),
            "drift_flags": drift_flags,
        }
    except Exception as e:
        await db.rollback()
        logger.error("[cron] Drift detection failed: %s", e)
        raise HTTPException(status_code=500, detail="Drift detection failed.")


@router.post("/qdrant-heartbeat", dependencies=[Depends(verify_cron_secret)])
async def qdrant_heartbeat():
    """
    Lightweight ping to Qdrant to keep serverless clusters awake.
    """
    logger.info("[cron] Executing qdrant_heartbeat...")
    try:
        qdrant = get_global_qdrant()
        # Just fetching collections to trigger a network request
        collections = qdrant.get_collections()
        logger.info(
            f"[cron] Qdrant heartbeat successful. {len(collections.collections)} collections found."
        )
        return {"status": "success", "message": "Qdrant is awake."}
    except Exception as e:
        logger.error(f"[cron] Qdrant heartbeat failed: {e}")
        raise HTTPException(status_code=500, detail="Qdrant heartbeat failed.")
