# app/routers/cron.py

import os
import logging
from fastapi import APIRouter, Header, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services.store import get_global_qdrant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron", tags=["Cron"])

def verify_cron_secret(x_cron_secret: str = Header(..., description="Secret token for cron job auth")):
    expected_secret = os.getenv("CRON_SECRET")
    if not expected_secret:
        logger.error("CRON_SECRET environment variable is not set on the server!")
        raise HTTPException(status_code=500, detail="Server misconfiguration: CRON_SECRET missing.")
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
        logger.info(f"[cron] Qdrant heartbeat successful. {len(collections.collections)} collections found.")
        return {"status": "success", "message": "Qdrant is awake."}
    except Exception as e:
        logger.error(f"[cron] Qdrant heartbeat failed: {e}")
        raise HTTPException(status_code=500, detail="Qdrant heartbeat failed.")
