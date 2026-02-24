# app/routers/session.py
#
# PURPOSE: Manages the lawyer's "active working set" of documents.
#
# A session is a temporary list of file IDs stored in Redis.
# Think of it as pulling specific folders from the filing cabinet onto your desk.
#
# Endpoints:
#   POST /session              — Create a new session with selected files
#   GET  /session/{id}         — See what files are in a session
#   POST /session/{id}/upload  — Upload a NEW file directly into a session
#   DELETE /session/{id}       — Close the session (clear the desk)

import hashlib
import os
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import File as FileModel, FileStatus
from app.redis_client import (
    add_file_to_session,
    create_session,
    get_file_progress,
    get_redis_client,
    get_session,
)
from app.tasks import is_scanned_pdf, process_digital_pdf, process_scanned_pdf

import hashlib

router = APIRouter(prefix="/session", tags=["Session"])


@router.post("/")
async def create_new_session(
    file_ids: List[int],
    org_id: str = "default_org",
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new working session with a list of READY file IDs.
    Returns a session_id to use in subsequent queries.
    """
    # Verify all files exist, belong to this org, and are READY
    result = await db.execute(
        select(FileModel).where(
            FileModel.id.in_(file_ids),
            FileModel.org_id == org_id,
        )
    )
    found_files = result.scalars().all()
    found_ids = {f.id for f in found_files}

    not_found = [fid for fid in file_ids if fid not in found_ids]
    if not_found:
        raise HTTPException(
            status_code=404,
            detail=f"Files not found or not owned by your org: {not_found}"
        )

    not_ready = [f.filename for f in found_files if f.status != FileStatus.READY]
    if not_ready:
        raise HTTPException(
            status_code=409,
            detail=f"These files are not ready yet: {not_ready}. Wait for processing to complete."
        )

    redis = get_redis_client()
    session_id = await create_session(file_ids, org_id, redis)
    await redis.aclose()

    return {
        "session_id": session_id,
        "file_count": len(file_ids),
        "message": "Session created. Use this session_id when querying.",
        "ttl_hours": 48,
    }


@router.get("/{session_id}")
async def get_session_info(
    session_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Returns the current state of a session — which files are in it, their status, and their filenames.
    """
    redis = get_redis_client()
    session = await get_session(session_id, redis)

    if not session:
        await redis.aclose()
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    # Get filenames from database
    file_ids_in_session = [int(fid) for fid in session["files"].keys()]
    filenames_map = {}
    if file_ids_in_session:
        result = await db.execute(
            select(FileModel.id, FileModel.filename).where(FileModel.id.in_(file_ids_in_session))
        )
        filenames_map = {row.id: row.filename for row in result.all()}

    # Enrich with progress for any PROCESSING files
    enriched_files = []
    for file_id_str, status in session["files"].items():
        file_id = int(file_id_str)
        entry = {
            "file_id": file_id,
            "filename": filenames_map.get(file_id, "Unknown File"),
            "status": status
        }
        if status == "PROCESSING":
            entry["progress_percent"] = await get_file_progress(file_id, redis)
        enriched_files.append(entry)

    await redis.aclose()

    return {
        "session_id": session_id,
        "org_id": session["org_id"],
        "files": enriched_files,
    }

@router.post("/{session_id}/upload")
async def upload_into_session(
    session_id: str,
    file: UploadFile = File(...),
    org_id: str = "default_org",
    db: AsyncSession = Depends(get_db),
):
    """
    Ad-hoc Upload: Upload a new file and immediately add it to an active session.
    The file is added as PROCESSING — the user can query other files while it processes.
    """
    redis = get_redis_client()
    session = await get_session(session_id, redis)

    if not session:
        await redis.aclose()
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    # Ensure upload directory exists
    os.makedirs("app/uploads", exist_ok=True)
    
    # We process files directly to disk to prevent OOM errors on large 50MB+ PDFs.
    file_hash_obj = hashlib.sha256()
    
    # We need a temporary unique filename until we have the final ID
    temp_file_path = f"app/uploads/temp_{uuid.uuid4().hex}.pdf"
    
    # ── Step 1: Stream bytes to disk and calculate hash simultaneously ───
    try:
        with open(temp_file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # Read in 1MB chunks
                if not chunk:
                    break
                file_hash_obj.update(chunk)
                f.write(chunk)
    except Exception as e:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        await redis.aclose()
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {str(e)}")
        
    file_hash = file_hash_obj.hexdigest()

    # ── Step 2: Duplicate Check (SHA256 hash) ───────────────────────────
    existing = await db.execute(
        select(FileModel).where(
            FileModel.file_hash == file_hash,
            FileModel.org_id == org_id,
            FileModel.status != FileStatus.FAILED
        )
    )
    existing_file = existing.scalars().first()
    if existing_file:
        # Delete the temp file we just wrote, since we already have it
        os.remove(temp_file_path)
        
        # File already exists — just add it to the session if READY
        if existing_file.status == FileStatus.READY:
            await add_file_to_session(session_id, existing_file.id, redis, "READY")
        else:
            await add_file_to_session(session_id, existing_file.id, redis, existing_file.status)
        await redis.aclose()
        return {
            "file_id": existing_file.id,
            "filename": file.filename,
            "status": "already_exists",
            "added_to_session": True,
        }

    # ── Step 3: Save PENDING record to Postgres ─────────────────────────
    new_file = FileModel(
        org_id=org_id,
        filename=file.filename,
        status=FileStatus.PENDING,
        file_hash=file_hash,
    )
    db.add(new_file)
    await db.commit()
    await db.refresh(new_file)
    file_id = new_file.id

    # ── Step 4: Add to session as PROCESSING immediately (Race Condition Fix) 
    await add_file_to_session(session_id, file_id, redis, "PROCESSING")
    await redis.aclose()

    # ── Step 5: Rename temp file to permanent ID-based name ─────────────
    final_file_path = f"app/uploads/{file_id}_{file.filename.replace(' ', '_')}"
    os.rename(temp_file_path, final_file_path)

    # ── Step 6: Detect PDF type (read directly from disk) ────────────────
    try:
        with open(final_file_path, "rb") as f:
            # We just need to check the first few pages, so reading the beginning is fine
            scanned = is_scanned_pdf(f.read(1024 * 1024 * 5)) # Only peek at first 5MB
    except Exception as e:
        scanned = False
        print(f"Warning: PDF scanning detection failed: {e}")

    # ── Step 7: Fire Celery Task ─────────────────────────────────────────
    if scanned:
        process_scanned_pdf.delay(file_id, org_id, file.filename, final_file_path)
    else:
        process_digital_pdf.delay(file_id, org_id, file.filename, final_file_path)

    return {
        "file_id": file_id,
        "filename": file.filename,
        "status": "PROCESSING",
        "added_to_session": True,
        "message": "File added to session. It is being processed. Query other files while you wait.",
    }


@router.delete("/{session_id}/files/{file_id}")
async def remove_file_from_session(session_id: str, file_id: int):
    """
    Removes a file from the active session (deselects it from the desk).
    Does NOT delete the file from Postgres or Qdrant — it stays in the filing cabinet.
    To permanently delete a file, use DELETE /files/{file_id} instead.
    """
    redis = get_redis_client()
    session = await get_session(session_id, redis)

    if not session:
        await redis.aclose()
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    if file_id not in session["files"]:
        await redis.aclose()
        raise HTTPException(status_code=404, detail=f"File {file_id} is not in this session.")

    # Remove just this file from the Redis hash
    await redis.hdel(f"session:{session_id}", str(file_id))
    await redis.aclose()

    return {
        "message": f"File {file_id} removed from session. It is still in the database.",
        "remaining_files": len(session["files"]) - 1,
    }


@router.post("/{session_id}/renew")
async def renew_session(session_id: str):
    """
    Resets the session TTL back to 48 hours without re-selecting files.
    Call this if the user is still actively working and the session is about to expire.
    """
    redis = get_redis_client()
    session_key = f"session:{session_id}"
    exists = await redis.exists(session_key)

    if not exists:
        await redis.aclose()
        raise HTTPException(status_code=404, detail="Session not found or already expired.")

    await redis.expire(session_key, 48 * 3600)
    await redis.aclose()

    return {"message": "Session renewed for another 48 hours."}


@router.delete("/{session_id}")

async def close_session(session_id: str):
    """Closes a session (clears the desk)."""
    redis = get_redis_client()
    await redis.delete(f"session:{session_id}")
    await redis.aclose()
    return {"message": "Session closed."}
