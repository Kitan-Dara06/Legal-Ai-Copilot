# app/routers/injest.py
#
# REPROCESS NOTE:
# Files can end up in Postgres as READY but missing from Qdrant if the worker
# crashed during upsert. Use POST /files/{file_id}/reprocess to re-dispatch
# the Celery task without needing to re-upload the file.
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
import io
import logging
import os
import uuid
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import File as FileModel
from app.models import FileStatus
from app.services.object_storage import upload_local_file_to_gcs
from app.tasks_map_reduce import dispatch_digital_pdf, dispatch_scanned_pdf
from app.tasks import (
    is_scanned_pdf,
    
    update_postgres_status_sync,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["Files"])
limiter = Limiter(key_func=get_remote_address)

MAX_FILE_SIZE_BYTES = (
    50 * 1024 * 1024
)  # 50 MB — outer guard (checked before we read the body)
MAX_SCANNED_PDF_SIZE_BYTES = (
    100 * 1024 * 1024
)  # 100 MB — scanned PDFs are large image stacks
MAX_DIGITAL_PDF_SIZE_BYTES = (
    50 * 1024 * 1024
)  # 50 MB — digital PDFs should be much smaller


# ── NOTE on org_id ────────────────────────────────────────────────────────────
# org_id is a simple namespace string (e.g. "lawfirm_abc") passed by the client.
# No JWT or login required. For a single-firm deployment, hardcode it or use an
# API key header that maps to an org_id. It is used purely for data isolation
# in Qdrant — so one org can never see another org's documents.
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/list")  # MUST be before /{file_id} to avoid route conflict
async def list_files(
    org_id: str = Depends(get_org_id_unified),
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    Returns READY files for this org with pagination.
    Use `limit` and `offset` for large libraries.
    """
    # Total count for the caller to know how many pages exist
    count_result = await db.execute(
        select(func.count(FileModel.id)).where(
            FileModel.org_id == org_id, FileModel.status == FileStatus.READY
        )
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(FileModel)
        .where(FileModel.org_id == org_id, FileModel.status == FileStatus.READY)
        .order_by(FileModel.upload_date.desc())
        .limit(limit)
        .offset(offset)
    )
    files = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "files": [
            {
                "file_id": f.id,
                "filename": f.filename,
                "upload_date": f.upload_date.isoformat(),
                "status": f.status,
            }
            for f in files
        ],
    }


def _upload_to_r2_and_enqueue(
    temp_file_path: str,
    file_id: int,
    org_id: str,
    filename: str,
    scanned: bool,
):
    """Runs after response is sent: upload to R2, enqueue Celery task, delete temp file."""
    import uuid
    blob_name = f"{uuid.uuid4().hex}_{filename.replace(' ', '_')}"
    try:
        upload_local_file_to_gcs(temp_file_path, blob_name)
        if scanned:
            dispatch_scanned_pdf.delay(file_id, org_id, filename, blob_name)
        else:
            dispatch_digital_pdf.delay(file_id, org_id, filename, blob_name)
    except Exception as e:
        logger.exception(
            "Background R2 upload or enqueue failed for file_id=%s: %s", file_id, e
        )
        try:
            update_postgres_status_sync(
                file_id, "FAILED", error=f"R2 upload failed: {e}"
            )
        except Exception as db_err:
            logger.warning("Could not mark file %s as FAILED: %s", file_id, db_err)
    finally:
        try:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        except OSError as e:
            logger.warning("Could not remove temp file %s: %s", temp_file_path, e)


@router.post("/upload", status_code=202)
@limiter.limit("10/minute")
async def upload_files(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload one or more PDF files (max 50 MB each, max 10 requests/min).
    Returns immediately — processing happens in the background via Celery.
    Poll GET /files/{file_id}/status to check when it's READY.
    """
    # Ensure upload directory exists
    os.makedirs("app/uploads", exist_ok=True)
    results = []

    for upload in files:
        # ── File size guard ──────────────────────────────────────────────────
        # content_length header is advisory — we enforce it by reading strictly
        if upload.size and upload.size > MAX_FILE_SIZE_BYTES:
            results.append(
                {
                    "filename": upload.filename,
                    "status": "error",
                    "message": f"File exceeds the 50 MB size limit ({upload.size / 1_048_576:.1f} MB).",
                }
            )
            continue

        # We process files directly to disk to prevent OOM errors on large 50MB+ PDFs.
        file_hash_obj = hashlib.sha256()

        # We need a temporary unique filename until we have the final ID
        temp_file_path = f"app/uploads/temp_{uuid.uuid4().hex}.pdf"

        # ── Step 1: Stream bytes to disk and calculate hash simultaneously ───
        try:
            total_bytes = 0
            with open(temp_file_path, "wb") as f:
                while True:
                    chunk = await upload.read(1024 * 1024)  # Read in 1MB chunks
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_SCANNED_PDF_SIZE_BYTES:
                        raise ValueError(f"File exceeds maximum allowed size of {MAX_SCANNED_PDF_SIZE_BYTES / 1_048_576:.1f} MB.")
                    file_hash_obj.update(chunk)
                    f.write(chunk)
        except Exception as e:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
            results.append(
                {
                    "filename": upload.filename,
                    "status": "error",
                    "message": f"Failed to save uploaded file: {str(e)}",
                }
            )
            continue

        file_hash = file_hash_obj.hexdigest()

        # ── Step 2: Duplicate Check (SHA256 hash) ───────────────────────────
        existing = await db.execute(
            select(FileModel).where(
                FileModel.file_hash == file_hash,
                FileModel.org_id == org_id,
                FileModel.status != FileStatus.FAILED,
            )
        )
        existing_file = existing.scalars().first()

        if existing_file:
            # Delete the temp file we just wrote, since we already have it
            os.remove(temp_file_path)
            results.append(
                {
                    "filename": upload.filename,
                    "status": "duplicate",
                    "message": f"This file was already uploaded (ID: {existing_file.id}).",
                    "file_id": existing_file.id,
                }
            )
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

        # ── Step 4: Detect PDF type (read directly from temp disk) ───────────
        try:
            with open(temp_file_path, "rb") as f:
                scanned = is_scanned_pdf(f.read(1024 * 1024 * 5))
        except Exception as e:
            scanned = False
            logger.warning("PDF scanning detection failed during upload: %s", e)

        # ── Step 4b: Enforce per-type size limits (after detection) ──────────
        try:
            actual_size = os.path.getsize(temp_file_path)
        except Exception:
            actual_size = None

        if actual_size is not None:
            if scanned and actual_size > MAX_SCANNED_PDF_SIZE_BYTES:
                os.remove(temp_file_path)
                results.append(
                    {
                        "filename": upload.filename,
                        "status": "error",
                        "message": f"Scanned PDF exceeds the 100 MB limit ({actual_size / 1_048_576:.1f} MB).",
                    }
                )
                continue

            if (not scanned) and actual_size > MAX_DIGITAL_PDF_SIZE_BYTES:
                os.remove(temp_file_path)
                results.append(
                    {
                        "filename": upload.filename,
                        "status": "error",
                        "message": f"Digital PDF exceeds the 50 MB limit ({actual_size / 1_048_576:.1f} MB).",
                    }
                )
                continue

        # ── Step 5: Schedule R2 upload + Celery enqueue in background ─────────
        # Return 202 immediately; upload and chunking start right after response.
        background_tasks.add_task(
            _upload_to_r2_and_enqueue,
            temp_file_path,
            file_id,
            org_id,
            upload.filename,
            scanned,
        )
        queue_name = "ocr" if scanned else "default"

        results.append(
            {
                "filename": upload.filename,
                "file_id": file_id,
                "status": "accepted",
                "queue": queue_name,
                "message": f"File received. Processing in background. Poll /files/{file_id}/status for updates.",
            }
        )

    return {"results": results}


@router.get("/{file_id}/status")
async def get_file_status(
    file_id: int,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll this endpoint to check if a file is done processing.
    Returns: { status: PENDING | PROCESSING | READY | FAILED }
    """
    result = await db.execute(
        select(FileModel).where(FileModel.id == file_id, FileModel.org_id == org_id)
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


@router.post("/{file_id}/reprocess", status_code=202)
async def reprocess_file(
    file_id: int,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Re-dispatches the Celery processing task for a file that is stuck in
    READY (indexed in Postgres but missing from Qdrant) or FAILED state.

    Does NOT require re-uploading — re-uses the existing GCS blob if present,
    or returns an error if the blob has already been deleted.
    """
    from app.services.object_storage import object_exists

    result = await db.execute(
        select(FileModel).where(
            FileModel.id == file_id,
            FileModel.org_id == org_id,
        )
    )
    file = result.scalar_one_or_none()

    if not file:
        raise HTTPException(status_code=404, detail="File not found.")

    if file.status == FileStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail="File is already being processed. Wait for it to complete.",
        )

    # Check that the object still exists in R2
    blob_name = f"{file_id}_{file.filename.replace(' ', '_')}"
    try:
        if not object_exists(blob_name):
            raise HTTPException(
                status_code=410,
                detail=(
                    f"Storage object '{blob_name}' no longer exists. "
                    "Please re-upload the file instead."
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not verify storage object: {e}",
        )

    # Reset status to PENDING so the UI shows processing in progress
    file.status = FileStatus.PENDING
    file.error_message = None
    await db.commit()

    # Re-dispatch to the appropriate queue
    # Re-detect scan type from stored object
    try:
        import uuid
        import os
        from app.services.object_storage import download_file_from_gcs

        temp_path = f"app/uploads/reprocess_{uuid.uuid4().hex}.pdf"
        try:
            download_file_from_gcs(blob_name, temp_path)
            with open(temp_path, "rb") as f:
                scanned = is_scanned_pdf(f.read(1024 * 1024 * 5))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception:
        scanned = False  # Default to digital on detection failure

    if scanned:
        dispatch_scanned_pdf.delay(file_id, org_id, file.filename, blob_name)
        queue_name = "ocr"
    else:
        dispatch_digital_pdf.delay(file_id, org_id, file.filename, blob_name)
        queue_name = "default"

    return {
        "file_id": file_id,
        "filename": file.filename,
        "status": "reprocessing",
        "queue": queue_name,
        "message": (f"Reprocessing started. Poll /files/{file_id}/status for updates."),
    }


@router.delete("/{file_id}")
async def delete_file(
    file_id: int,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Permanently deletes a file from Postgres, Qdrant, and all active Redis sessions.
    This is a hard delete — the file is gone from the filing cabinet entirely.
    To just remove a file from the current session (without deleting it),
    use DELETE /session/{session_id}/files/{file_id} instead.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    from app.redis_client import get_redis_client, remove_file_from_all_sessions
    from app.services.store import get_global_qdrant

    result = await db.execute(
        select(FileModel).where(FileModel.id == file_id, FileModel.org_id == org_id)
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
                must=[
                    FieldCondition(key="file_id", match=MatchValue(value=file_id)),
                    FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                ]
            ),
        )
    except Exception as e:
        print(f"Warning: Failed to delete from Qdrant (might not exist yet): {e}")

    # 2. Remove from all active Redis sessions (Zombie File Fix)
    redis = get_redis_client()
    await remove_file_from_all_sessions(file_id, redis)

    # 3. Clean up any orphaned GCS blob (if file was deleted while PENDING)
    from app.services.object_storage import delete_file_from_gcs

    blob_name = f"{file_id}_{file.filename.replace(' ', '_')}"
    delete_file_from_gcs(blob_name)

    # 4. Delete record from Postgres
    await db.delete(file)
    await db.commit()

    return {"message": f"File '{file.filename}' (ID: {file_id}) permanently deleted."}
