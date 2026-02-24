# app/routers/injest.py
#
# PURPOSE: Handles file uploads and file management.
#
# Flow:
#   1. Receive PDF.
#   2. Check for duplicates (SHA256 hash).
#   3. Create PENDING record in Postgres immediately.
#   4. Detect if PDF is digital or scanned.
#   5. Fire Celery background task — return instantly.
#   6. Return { file_id, status: "accepted" }.
#
# The heavy work (parsing, embedding, Qdrant) happens in app/tasks.py.

import hashlib
import os
import io
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import File as FileModel, FileStatus
from app.tasks import is_scanned_pdf, process_digital_pdf, process_scanned_pdf

router = APIRouter(prefix="/files", tags=["Files"])


# ── NOTE on org_id ────────────────────────────────────────────────────────────
# org_id is a simple namespace string (e.g. "lawfirm_abc") passed by the client.
# No JWT or login required. For a single-firm deployment, hardcode it or use an
# API key header that maps to an org_id. It is used purely for data isolation
# in Qdrant — so one org can never see another org's documents.
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/list")  # MUST be before /{file_id} to avoid route conflict
async def list_files(
    org_id: str = "default_org",
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all READY files for this org.
    This is what the UI shows when the user wants to select files for a session.
    """
    result = await db.execute(
        select(FileModel).where(
            FileModel.org_id == org_id,
            FileModel.status == FileStatus.READY
        ).order_by(FileModel.upload_date.desc())
    )
    files = result.scalars().all()

    return {
        "files": [
            {
                "file_id": f.id,
                "filename": f.filename,
                "upload_date": f.upload_date.isoformat(),
                "status": f.status,
            }
            for f in files
        ]
    }


@router.post("/upload", status_code=202)
async def upload_files(
    files: List[UploadFile] = File(...),
    org_id: str = "default_org",
    db: AsyncSession = Depends(get_db),
):
    """
    Upload one or more PDF files.
    Returns immediately — processing happens in the background via Celery.
    Poll GET /files/{file_id}/status to check when it's READY.
    """
    # Ensure upload directory exists
    os.makedirs("app/uploads", exist_ok=True)
    results = []

    for upload in files:
        # We process files directly to disk to prevent OOM errors on large 50MB+ PDFs.
        file_hash_obj = hashlib.sha256()
        
        # We need a temporary unique filename until we have the final ID
        temp_file_path = f"app/uploads/temp_{uuid.uuid4().hex}.pdf"
        
        # ── Step 1: Stream bytes to disk and calculate hash simultaneously ───
        try:
            with open(temp_file_path, "wb") as f:
                while True:
                    chunk = await upload.read(1024 * 1024)  # Read in 1MB chunks
                    if not chunk:
                        break
                    file_hash_obj.update(chunk)
                    f.write(chunk)
        except Exception as e:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            results.append({
                "filename": upload.filename,
                "status": "error",
                "message": f"Failed to save uploaded file: {str(e)}"
            })
            continue
            
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
            results.append({
                "filename": upload.filename,
                "status": "duplicate",
                "message": f"This file was already uploaded (ID: {existing_file.id}).",
                "file_id": existing_file.id,
            })
            continue

        # ── Step 3: Save PENDING record to Postgres ─────────────────────────
        new_file = FileModel(
            org_id=org_id,
            filename=upload.filename,
            status=FileStatus.PENDING,
            file_hash=file_hash,
        )
        db.add(new_file)
        await db.commit()
        await db.refresh(new_file)
        file_id = new_file.id
        
        # ── Step 4: Rename temp file to permanent ID-based name ─────────────
        final_file_path = f"app/uploads/{file_id}_{upload.filename.replace(' ', '_')}"
        os.rename(temp_file_path, final_file_path)

        # ── Step 5: Detect PDF type (read directly from disk) ────────────────
        try:
            with open(final_file_path, "rb") as f:
                # We just need to check the first few pages, so reading the beginning is fine
                scanned = is_scanned_pdf(f.read(1024 * 1024 * 5)) # Only peek at first 5MB
        except Exception as e:
            scanned = False
            print(f"Warning: PDF scanning detection failed: {e}")

        # ── Step 6: Fire Celery Task ─────────────────────────────────────────
        if scanned:
            process_scanned_pdf.delay(file_id, org_id, upload.filename, final_file_path)
            queue_name = "ocr"
        else:
            process_digital_pdf.delay(file_id, org_id, upload.filename, final_file_path)
            queue_name = "default"

        results.append({
            "filename": upload.filename,
            "file_id": file_id,
            "status": "accepted",
            "queue": queue_name,
            "message": f"File received. Processing in background. Poll /files/{file_id}/status for updates.",
        })

    return {"results": results}


@router.get("/{file_id}/status")
async def get_file_status(
    file_id: int,
    org_id: str = "default_org",
    db: AsyncSession = Depends(get_db),
):
    """
    Poll this endpoint to check if a file is done processing.
    Returns: { status: PENDING | PROCESSING | READY | FAILED }
    """
    result = await db.execute(
        select(FileModel).where(
            FileModel.id == file_id,
            FileModel.org_id == org_id
        )
    )
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found.")

    return {
        "file_id": file.id,
        "filename": file.filename,
        "status": file.status,
        "error": file.error_message,
    }


@router.delete("/{file_id}")
async def delete_file(
    file_id: int,
    org_id: str = "default_org",
    db: AsyncSession = Depends(get_db),
):
    """
    Permanently deletes a file from Postgres, Qdrant, and all active Redis sessions.
    This is a hard delete — the file is gone from the filing cabinet entirely.
    To just remove a file from the current session (without deleting it),
    use DELETE /session/{session_id}/files/{file_id} instead.
    """
    from app.services.store import get_global_qdrant
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from app.redis_client import remove_file_from_all_sessions, get_redis_client

    result = await db.execute(
        select(FileModel).where(
            FileModel.id == file_id,
            FileModel.org_id == org_id
        )
    )
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found.")

    # 1. Delete vectors from Qdrant
    qdrant = get_global_qdrant()
    try:
        qdrant.delete(
            collection_name="legal_chunks",
            points_selector=Filter(
                must=[FieldCondition(key="file_id", match=MatchValue(value=file_id))]
            ),
        )
    except Exception as e:
        print(f"Warning: Failed to delete from Qdrant (might not exist yet): {e}")

    # 2. Remove from all active Redis sessions (Zombie File Fix)
    redis = get_redis_client()
    await remove_file_from_all_sessions(file_id, redis)
    await redis.aclose()

    # 3. Delete record from Postgres
    await db.delete(file)
    await db.commit()

    return {"message": f"File '{file.filename}' (ID: {file_id}) permanently deleted."}
