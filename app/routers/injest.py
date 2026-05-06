# app/routers/injest.py
#
# PURPOSE: Handles file uploads and file management.
# Unified Lex Architecture: Uses Document and Workspace models.

import hashlib
import io
import logging
import os
import re
import uuid
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import Document, DocumentStatus, DocumentType, Workspace
from app.services.object_storage import generate_presigned_upload, object_exists
from app.tasks import (
    process_digital_pdf,
    process_scanned_pdf,
)
from app.utils import is_scanned_pdf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["Files"])
limiter = Limiter(key_func=get_remote_address)

MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100MB strictly enforced by R2
MAX_DOCS_PER_WORKSPACE = 200


class UploadRequest(BaseModel):
    filename: str
    workspace_id: uuid.UUID
    file_size: int


class ConfirmUploadRequest(BaseModel):
    document_id: uuid.UUID
    workspace_id: uuid.UUID
    filename: str
    blob_name: str
    file_hash: str | None = None  # SHA-256 for deduplication
    scanned: bool = False


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

    stmt = select(Document).where(
        Document.org_id == org_uuid, Document.status == DocumentStatus.READY
    )
    if workspace_id:
        stmt = stmt.where(Document.workspace_id == workspace_id)

    count_stmt = select(func.count(Document.id)).where(
        Document.org_id == org_uuid, Document.status == DocumentStatus.READY
    )
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


@router.post("/request-upload", status_code=200)
@limiter.limit("10/minute")
async def request_upload(
    request: Request,
    req: UploadRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1 of 'Valet Key' Pattern.
    Returns a pre-signed R2 POST URL so the frontend uploads directly.
    """
    org_uuid = uuid.UUID(org_id)

    # 1. Verify workspace access
    ws_res = await db.execute(
        select(Workspace).where(
            Workspace.id == req.workspace_id, Workspace.org_id == org_uuid
        )
    )
    if not ws_res.scalar_one_or_none():
        raise HTTPException(
            status_code=404, detail="Workspace access denied or not found."
        )

    # 2. Check 100 Docs per Workspace limit
    count_res = await db.execute(
        select(func.count(Document.id)).where(
            Document.workspace_id == req.workspace_id,
            Document.status != DocumentStatus.FAILED,
        )
    )
    doc_count = count_res.scalar_one()
    if doc_count >= MAX_DOCS_PER_WORKSPACE:
        raise HTTPException(
            status_code=403,
            detail=f"Workspace limit reached ({MAX_DOCS_PER_WORKSPACE} documents).",
        )

    if req.file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 100MB limit.")

    # 3. Generate Pre-signed URL
    document_id = uuid.uuid4()
    safe_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", req.filename)
    blob_name = f"{document_id.hex}_{safe_filename}"

    try:
        presigned = generate_presigned_upload(
            blob_name, max_size_bytes=MAX_FILE_SIZE_BYTES
        )
    except Exception as e:
        logger.error("R2 generation failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to generate upload URL.")

    return {
        "document_id": str(document_id),
        "blob_name": blob_name,
        "url": presigned["url"],
        "fields": presigned["fields"],
    }


@router.post("/confirm-upload", status_code=202)
@limiter.limit("20/minute")
async def confirm_upload(
    request: Request,
    req: ConfirmUploadRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 2 of 'Valet Key' Pattern.
    Client confirms upload finished. Backend verifies and queues Celery task.

    FR-WS-03 (Deduplication): If file_hash is provided and a READY document with
    the same hash exists in this org, we link to the existing document instead
    of re-ingesting.
    """
    org_uuid = uuid.UUID(org_id)

    # 0. SHA-256 deduplication check
    if req.file_hash:
        existing = await db.execute(
            select(Document).where(
                Document.file_hash == req.file_hash,
                Document.org_id == org_uuid,
                Document.status == DocumentStatus.READY,
            )
        )
        existing_doc = existing.scalar_one_or_none()
        if existing_doc:
            logger.info(
                "[dedup] Document %s already exists as %s — linking workspace %s",
                req.filename,
                existing_doc.id,
                req.workspace_id,
            )
            # Link: create a new document row referencing the same R2 key
            linked_doc = Document(
                workspace_id=req.workspace_id,
                org_id=org_uuid,
                filename=req.filename,
                file_hash=req.file_hash,
                r2_key=existing_doc.r2_key,
                status=DocumentStatus.READY,
                file_type=existing_doc.file_type,
                intelligence_stages_complete=existing_doc.intelligence_stages_complete,
            )
            db.add(linked_doc)
            await db.commit()
            await db.refresh(linked_doc)
            return {
                "status": "duplicate",
                "document_id": str(linked_doc.id),
                "source_document_id": str(existing_doc.id),
                "message": "Linked to existing document (no re-ingestion needed).",
            }

    # 1. Verify the object actually arrived in R2
    if not object_exists(req.blob_name):
        raise HTTPException(status_code=404, detail="File not found in storage.")

    # 2. Create the Document row in PostgreSQL
    new_doc = Document(
        id=req.document_id,
        workspace_id=req.workspace_id,
        org_id=org_uuid,
        filename=req.filename,
        file_hash=req.file_hash,
        r2_key=req.blob_name,
        status=DocumentStatus.PENDING,
        file_type=DocumentType.SCANNED_PDF if req.scanned else DocumentType.DIGITAL_PDF,
    )
    db.add(new_doc)
    await db.commit()

    # 3. Fire-and-forget Celery pipeline trigger
    if req.scanned:
        process_scanned_pdf.delay(
            str(req.document_id),
            str(req.workspace_id),
            str(org_id),
            req.filename,
            req.blob_name,
        )
    else:
        process_digital_pdf.delay(
            str(req.document_id),
            str(req.workspace_id),
            str(org_id),
            req.filename,
            req.blob_name,
        )

    return {"status": "accepted", "document_id": str(req.document_id)}


@router.get("/{document_id}/status")
async def get_document_status(
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    org_uuid = uuid.UUID(org_id)
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.org_id == org_uuid)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    return {
        "document_id": str(doc.id),
        "status": doc.status,
        "stages": doc.intelligence_stages_complete,
        "error": doc.error_message,
    }


@router.post("/{document_id}/reprocess", status_code=202)
@limiter.limit("5/minute")
async def reprocess_document(
    request: Request,
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrigger the intelligence pipeline for a document.
    Resets its status to PENDING and dispatches a new Celery task.
    """
    org_uuid = uuid.UUID(org_id)
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.org_id == org_uuid)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Reset state
    doc.status = DocumentStatus.PENDING
    doc.intelligence_stages_complete = None
    doc.error_message = None
    await db.commit()

    # Determine if scanned based on stored file_type
    is_scanned = doc.file_type == DocumentType.SCANNED_PDF

    if is_scanned:
        process_scanned_pdf.delay(
            str(doc.id),
            str(doc.workspace_id),
            str(org_id),
            doc.filename,
            doc.r2_key,
        )
    else:
        process_digital_pdf.delay(
            str(doc.id),
            str(doc.workspace_id),
            str(org_id),
            doc.filename,
            doc.r2_key,
        )

    logger.info("[reprocess] Dispatched reprocessing for document %s", doc.id)
    return {"status": "accepted", "document_id": str(doc.id)}


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a document from the workspace."""
    org_uuid = uuid.UUID(org_id)
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.org_id == org_uuid)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    await db.delete(doc)
    await db.commit()
    logger.info("[files] Deleted document %s", document_id)
