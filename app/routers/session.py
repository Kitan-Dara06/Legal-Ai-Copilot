# app/routers/session.py
#
# PURPOSE: Manages the lawyer's "active working set" of documents.
# Lex Unified Architecture: Uses Document and Workspace models.

import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import Document, DocumentStatus
from app.redis_client import (
    add_file_to_session,
    create_session,
    get_file_progress,
    get_redis_client,
    get_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/session", tags=["Session"])
limiter = Limiter(key_func=get_remote_address)

MAX_DIGITAL_FILE_SIZE_BYTES = 10 * 1024 * 1024
MAX_SCANNED_FILE_SIZE_BYTES = 100 * 1024 * 1024

@router.post("/", summary="Create Workspace Session")
@limiter.limit("20/minute")
async def create_new_session(
    request: Request,
    file_ids: List[uuid.UUID],
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Create a new working session with a list of READY document IDs."""
    org_uuid = uuid.UUID(org_id)
    
    if len(file_ids) > 100:
        raise HTTPException(status_code=400, detail="Cannot exceed 100 files per session.")

    result = await db.execute(
        select(Document).where(Document.id.in_(file_ids), Document.org_id == org_uuid)
    )
    found_docs = result.scalars().all()
    found_ids = {d.id for d in found_docs}

    not_found = [fid for fid in file_ids if fid not in found_ids]
    if not_found:
        raise HTTPException(status_code=404, detail="One or more documents could not be accessed.")

    not_ready = [d.filename for d in found_docs if d.status != DocumentStatus.READY]
    if not_ready:
        raise HTTPException(status_code=409, detail="One or more documents are not ready.")

    redis = get_redis_client()
    # Convert UUIDs to strings for Redis
    file_id_strs = [str(fid) for fid in file_ids]
    session_id = await create_session(file_id_strs, org_id, redis)

    return {
        "session_id": session_id,
        "file_count": len(file_ids),
        "message": "Session created.",
    }

@router.get("/{session_id}")
async def get_session_info(
    session_id: str,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    redis = get_redis_client()
    session = await get_session(session_id, redis)
    if not session or session["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Session not found.")

    doc_ids_in_session = [uuid.UUID(fid) for fid in session["files"].keys()]
    doc_map = {}
    if doc_ids_in_session:
        org_uuid = uuid.UUID(org_id)
        stmt = select(Document.id, Document.filename, Document.status).where(
            Document.id.in_(doc_ids_in_session), Document.org_id == org_uuid
        )
        result = await db.execute(stmt)
        doc_map = {row.id: {"filename": row.filename, "status": row.status} for row in result.all()}

    enriched_files = []
    for fid_str, status in session["files"].items():
        fid = uuid.UUID(fid_str)
        info = doc_map.get(fid, {"filename": "Unknown", "status": status})
        entry = {
            "file_id": fid_str,
            "filename": info["filename"],
            "status": info["status"],
        }
        if info["status"] == DocumentStatus.PROCESSING:
            entry["progress_percent"] = await get_file_progress(fid_str, redis)
        enriched_files.append(entry)

    return {
        "session_id": session_id,
        "org_id": session["org_id"],
        "files": enriched_files,
    }

@router.delete("/{session_id}/files/{file_id}")
async def remove_file_from_session(
    session_id: str,
    file_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
):
    redis = get_redis_client()
    session = await get_session(session_id, redis)
    if not session or session["org_id"] != org_id:
        raise HTTPException(status_code=404, detail="Session not found.")

    if str(file_id) not in session["files"]:
        raise HTTPException(status_code=404, detail="File not in session.")

    await redis.hdel(f"session:{session_id}", str(file_id))
    return {"message": "File removed from session."}
