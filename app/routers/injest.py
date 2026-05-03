# app/routers/injest.py
#
# PURPOSE: Handles file uploads and file management.
# Unified Lex Architecture: Uses Document and Workspace models.

import hashlib
import io
import logging
import os
import uuid
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    Query,
)
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import Document, DocumentStatus, DocumentType, Workspace
from app.services.object_storage import upload_local_file_to_gcs
from app.tasks import (
    process_digital_pdf,
    process_scanned_pdf,
    update_postgres_status_sync,
)
from app.utils import is_scanned_pdf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["Files"])
limiter = Limiter(key_func=get_remote_address)

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_SCANNED_PDF_SIZE_BYTES = 100 * 1024 * 1024
MAX_DIGITAL_PDF_SIZE_BYTES = 50 * 1024 * 1024

@router.get(
    "/list",
    summary="List Organization Documents",
)
@limiter.limit("60/minute")
async def list_files(
    request: Request,
    org_id: str = Depends(get_org_id_unified),
    workspace_id: Optional[uuid.UUID] = Query(None),
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Returns READY documents for this org/workspace with pagination."""
    org_uuid = uuid.UUID(org_id)
    
    stmt = select(Document).where(Document.org_id == org_uuid, Document.status == DocumentStatus.READY)
    if workspace_id:
        stmt = stmt.where(Document.workspace_id == workspace_id)
        
    count_stmt = select(func.count(Document.id)).where(Document.org_id == org_uuid, Document.status == DocumentStatus.READY)
    if workspace_id:
        count_stmt = count_stmt.where(Document.workspace_id == workspace_id)
        
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    result = await db.execute(
        stmt.order_by(Document.upload_date.desc()).limit(limit).offset(offset)
    )
    docs = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "files": [
            {
                "file_id": str(d.id),
                "workspace_id": str(d.workspace_id),
                "filename": d.filename,
                "upload_date": d.upload_date.isoformat(),
                "status": d.status,
            }
            for d in docs
        ],
    }

def _upload_to_r2_and_enqueue(
    temp_file_path: str,
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
    org_id: uuid.UUID,
    filename: str,
    scanned: bool,
):
    """Background task: upload to R2 and enqueue Celery."""
    blob_name = f"{uuid.uuid4().hex}_{filename.replace(' ', '_')}"
    try:
        upload_local_file_to_gcs(temp_file_path, blob_name)
        if scanned:
            process_scanned_pdf.delay(str(document_id), str(workspace_id), str(org_id), filename, blob_name)
        else:
            process_digital_pdf.delay(str(document_id), str(workspace_id), str(org_id), filename, blob_name)
    except Exception as e:
        logger.exception("Background upload/enqueue failed: %s", e)
        update_postgres_status_sync(str(document_id), DocumentStatus.FAILED, error=f"Storage upload failed: {e}")
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

@router.post("/upload", status_code=202)
@limiter.limit("10/minute")
async def upload_files(
    request: Request,
    background_tasks: BackgroundTasks,
    workspace_id: uuid.UUID = Query(...),
    files: List[UploadFile] = File(...),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Upload documents to a specific workspace."""
    import tempfile
    org_uuid = uuid.UUID(org_id)
    
    # Verify workspace belongs to org
    ws_res = await db.execute(select(Workspace).where(Workspace.id == workspace_id, Workspace.org_id == org_uuid))
    if not ws_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace access denied or not found.")

    import re
    results = []
    for upload in files:
        if upload.size and upload.size > MAX_FILE_SIZE_BYTES:
            results.append({"filename": upload.filename, "status": "error", "message": "File too large."})
            continue

        safe_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', upload.filename)
        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"lex_upload_{uuid.uuid4().hex}.pdf")
        file_hash_obj = hashlib.sha256()
        
        try:
            total_bytes = 0
            with open(temp_file_path, "wb") as f:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk: break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_FILE_SIZE_BYTES:
                        raise ValueError("File too large.")
                    file_hash_obj.update(chunk)
                    f.write(chunk)
        except Exception as e:
            if os.path.exists(temp_file_path): os.remove(temp_file_path)
            results.append({"filename": upload.filename, "status": "error", "message": str(e)})
            continue

        file_hash = file_hash_obj.hexdigest()

        # Duplicate Check
        existing = await db.execute(select(Document).where(Document.file_hash == file_hash, Document.workspace_id == workspace_id, Document.status != DocumentStatus.FAILED))
        if existing.scalars().first():
            os.remove(temp_file_path)
            results.append({"filename": upload.filename, "status": "duplicate"})
            continue

        # Save PENDING Document
        new_doc = Document(
            workspace_id=workspace_id,
            org_id=org_uuid,
            filename=safe_filename,
            file_hash=file_hash,
            status=DocumentStatus.PENDING,
            r2_key="PENDING" # Placeholder
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)

        # Detect scan
        with open(temp_file_path, "rb") as f:
            scanned = is_scanned_pdf(f.read(1024 * 512))

        background_tasks.add_task(
            _upload_to_r2_and_enqueue,
            temp_file_path,
            new_doc.id,
            workspace_id,
            org_uuid,
            safe_filename,
            scanned
        )

        results.append({
            "filename": safe_filename,
            "document_id": str(new_doc.id),
            "status": "accepted"
        })

    return {"results": results}

@router.get("/{document_id}/status")
async def get_document_status(
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    org_uuid = uuid.UUID(org_id)
    result = await db.execute(select(Document).where(Document.id == document_id, Document.org_id == org_uuid))
    doc = result.scalar_one_or_none()
    if not doc: raise HTTPException(status_code=404, detail="Document not found.")
    
    return {
        "document_id": str(doc.id),
        "status": doc.status,
        "stages": doc.intelligence_stages_complete,
        "error": doc.error_message
    }
