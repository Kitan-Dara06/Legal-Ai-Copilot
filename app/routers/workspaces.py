"""
Workspace API
=============
CRUD operations for org-scoped "deal folders" (workspaces).

FR-WS-01: Workspaces are strictly org-scoped.
FR-WS-02: Documents are uploaded into a workspace context.
"""

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.database import get_db
from app.dependencies import get_org_id_unified, get_redis
from app.models import (
    DeadlineRegistry,
    DefinedTermRegistry,
    Document,
    DocumentStatus,
    DocumentType,
    IntelligenceStatus,
    WorkflowExecution,
    Workspace,
    WorkspaceSession,
)
from app.redis_client import create_session as redis_create_session
from app.services.object_storage import object_exists, upload_bytes
from app.utils import is_scanned_pdf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["Workspaces"])
limiter = Limiter(key_func=get_remote_address)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────


class CreateWorkspaceRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateWorkspaceRequest(BaseModel):
    name: str | None = None
    description: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# FR-WS-01: Workspace CRUD
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
@limiter.limit("20/minute")
async def create_workspace(
    request: Request,
    req: CreateWorkspaceRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Create a new workspace (deal folder) scoped to the current org."""
    org_uuid = uuid.UUID(org_id)

    workspace = Workspace(
        org_id=org_uuid,
        name=req.name.strip(),
        description=req.description.strip() if req.description else None,
        intelligence_status=IntelligenceStatus.PENDING,
    )
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)

    logger.info(
        "[workspaces] Created workspace %s (%s) for org %s",
        workspace.id,
        workspace.name,
        org_id,
    )

    return {
        "workspace_id": str(workspace.id),
        "name": workspace.name,
        "description": workspace.description,
        "intelligence_status": workspace.intelligence_status.value,
        "document_count": workspace.document_count,
        "created_at": workspace.created_at.isoformat(),
    }


@router.get("")
async def list_workspaces(
    request: Request,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """List all workspaces for the current org, with document counts."""
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(Workspace)
        .where(Workspace.org_id == org_uuid, Workspace.archived_at.is_(None))
        .order_by(Workspace.last_active_at.desc())
    )
    workspaces = result.scalars().all()

    return [
        {
            "workspace_id": str(ws.id),
            "name": ws.name,
            "description": ws.description,
            "intelligence_status": ws.intelligence_status.value,
            "document_count": ws.document_count,
            "created_at": ws.created_at.isoformat(),
            "last_active_at": ws.last_active_at.isoformat(),
        }
        for ws in workspaces
    ]


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    include_docs: bool = Query(True, description="Include document list"),
):
    """Get workspace details with optional document list."""
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
            Workspace.archived_at.is_(None),
        )
    )
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    response = {
        "workspace_id": str(ws.id),
        "name": ws.name,
        "description": ws.description,
        "intelligence_status": ws.intelligence_status.value,
        "document_count": ws.document_count,
        "created_at": ws.created_at.isoformat(),
        "last_active_at": ws.last_active_at.isoformat(),
    }

    if include_docs:
        doc_result = await db.execute(
            select(
                Document.id,
                Document.filename,
                Document.status,
                Document.file_type,
                Document.file_hash,
                Document.intelligence_stages_complete,
                Document.error_message,
                Document.upload_date,
            )
            .where(
                Document.workspace_id == workspace_id,
                Document.org_id == org_uuid,
            )
            .order_by(Document.upload_date.desc())
        )
        docs = doc_result.all()
        response["documents"] = [
            {
                "document_id": str(d.id),
                "filename": d.filename,
                "status": d.status.value if d.status else None,
                "file_type": d.file_type.value if d.file_type else None,
                "file_hash": d.file_hash,
                "stages": d.intelligence_stages_complete,
                "error": d.error_message,
                "upload_date": d.upload_date.isoformat(),
            }
            for d in docs
        ]

    return response


@router.patch("/{workspace_id}")
async def update_workspace(
    workspace_id: uuid.UUID,
    req: UpdateWorkspaceRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Update workspace name or description."""
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
            Workspace.archived_at.is_(None),
        )
    )
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    if req.name is not None:
        ws.name = req.name.strip()
    if req.description is not None:
        ws.description = req.description.strip() if req.description else None
    ws.last_active_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(ws)

    return {
        "workspace_id": str(ws.id),
        "name": ws.name,
        "description": ws.description,
    }


@router.delete("/{workspace_id}", status_code=204)
async def archive_workspace(
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a workspace by setting archived_at."""
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
            Workspace.archived_at.is_(None),
        )
    )
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    ws.archived_at = datetime.now(timezone.utc)
    await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# FR-WS-02/03: Document Upload
# ─────────────────────────────────────────────────────────────────────────────

MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100MB
MAX_DOCS_PER_WORKSPACE = 200


@router.post("/{workspace_id}/documents", status_code=202)
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a PDF or DOCX to a workspace.

    Steps:
      1. Verify workspace access
      2. Read file bytes + compute SHA-256 hash
      3. Dedup check: skip re-ingestion if identical file exists in org
      4. Stream to R2
      5. Create PENDING Document record
      6. Dispatch Celery intelligence pipeline
    """
    org_uuid = uuid.UUID(org_id)

    # 1. Verify workspace access
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
            Workspace.archived_at.is_(None),
        )
    )
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # Validate file type
    filename = (file.filename or "document.pdf").strip()
    if not filename.lower().endswith((".pdf", ".docx")):
        raise HTTPException(
            status_code=400,
            detail="Only PDF and DOCX files are supported.",
        )

    # 2. Read file bytes + compute SHA-256
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds 100MB limit.")

    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    # 3. Dedup check
    existing = await db.execute(
        select(Document).where(
            Document.file_hash == file_hash,
            Document.org_id == org_uuid,
            Document.status == DocumentStatus.READY,
        )
    )
    existing_doc = existing.scalar_one_or_none()
    if existing_doc:
        # Link to existing document without re-ingesting
        linked_doc = Document(
            workspace_id=workspace_id,
            org_id=org_uuid,
            filename=filename,
            file_hash=file_hash,
            file_type=existing_doc.file_type,
            r2_key=existing_doc.r2_key,
            status=DocumentStatus.READY,
            intelligence_stages_complete=existing_doc.intelligence_stages_complete,
        )
        db.add(linked_doc)
        await db.commit()
        await db.refresh(linked_doc)

        logger.info(
            "[dedup] %s linked to existing doc %s in workspace %s",
            filename,
            existing_doc.id,
            workspace_id,
        )
        return {
            "status": "duplicate",
            "document_id": str(linked_doc.id),
            "source_document_id": str(existing_doc.id),
            "message": "Linked to existing document (no re-ingestion needed).",
        }

    # 4. Enforce workspace document limit
    count_res = await db.execute(
        select(func.count(Document.id)).where(
            Document.workspace_id == workspace_id,
            Document.status != DocumentStatus.FAILED,
        )
    )
    if count_res.scalar_one() >= MAX_DOCS_PER_WORKSPACE:
        raise HTTPException(
            status_code=403,
            detail=f"Workspace limit reached ({MAX_DOCS_PER_WORKSPACE} documents).",
        )

    # 5. Stream to R2
    document_id = uuid.uuid4()
    safe_filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    blob_name = f"{document_id.hex}_{safe_filename}"

    content_type = (
        "application/pdf"
        if filename.lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    try:
        upload_bytes(raw_bytes, blob_name, content_type=content_type)
    except Exception as e:
        logger.error("[workspaces] R2 upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to store file.")

    # 6. Determine file type and create Document record
    is_scanned = filename.lower().endswith(".pdf") and is_scanned_pdf(raw_bytes)
    file_type = DocumentType.SCANNED_PDF if is_scanned else DocumentType.DIGITAL_PDF

    new_doc = Document(
        id=document_id,
        workspace_id=workspace_id,
        org_id=org_uuid,
        filename=filename,
        file_hash=file_hash,
        r2_key=blob_name,
        status=DocumentStatus.PENDING,
        file_type=file_type,
    )
    db.add(new_doc)
    await db.commit()

    # 7. Dispatch Celery pipeline
    if is_scanned:
        celery_app.send_task(
            "app.tasks.process_scanned_pdf",
            kwargs={
                "document_id": str(document_id),
                "workspace_id": str(workspace_id),
                "org_id": str(org_id),
                "filename": filename,
                "blob_name": blob_name,
            },
            queue="ocr",
        )
    else:
        celery_app.send_task(
            "app.tasks.process_digital_pdf",
            kwargs={
                "document_id": str(document_id),
                "workspace_id": str(workspace_id),
                "org_id": str(org_id),
                "filename": filename,
                "blob_name": blob_name,
            },
            queue="default",
        )

    logger.info(
        "[workspaces] Uploaded %s -> doc %s in workspace %s",
        filename,
        document_id,
        workspace_id,
    )

    return {
        "status": "accepted",
        "document_id": str(document_id),
        "file_hash": file_hash,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FR-WS-04: Persistent Workspace Sessions
# ─────────────────────────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    document_ids: list[uuid.UUID]
    user_id: uuid.UUID | None = None


class UpdateSessionContextRequest(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    context: dict | None = None


@router.post("/{workspace_id}/sessions", status_code=201)
@limiter.limit("20/minute")
async def create_workspace_session(
    request: Request,
    workspace_id: uuid.UUID,
    req: CreateSessionRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a persistent workspace session.

    Stores the session in Postgres (survives restarts) and carries:
    - document_subset: the working set of documents
    - context: prior REASON findings, user preferences, etc.
    """
    org_uuid = uuid.UUID(org_id)

    # Verify workspace access
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
            Workspace.archived_at.is_(None),
        )
    )
    ws = ws_result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # Verify documents belong to this org
    if req.document_ids:
        doc_result = await db.execute(
            select(Document.id).where(
                Document.id.in_(req.document_ids),
                Document.org_id == org_uuid,
            )
        )
        found_ids = {row[0] for row in doc_result.all()}
        missing = [str(did) for did in req.document_ids if did not in found_ids]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Documents not found in this org: {missing}",
            )

    # Resolve user_id
    user_id = req.user_id
    if user_id is None:
        # Fallback: find the first user in this org
        from app.models import UserOrgMembership

        mem_result = await db.execute(
            select(UserOrgMembership.user_id)
            .where(UserOrgMembership.org_id == org_uuid)
            .limit(1)
        )
        first_user = mem_result.scalar_one_or_none()
        if first_user:
            user_id = first_user
        else:
            # Absolute fallback — create with a nil UUID
            user_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

    session = WorkspaceSession(
        workspace_id=workspace_id,
        user_id=user_id,
        document_subset=req.document_ids or [],
    )
    db.add(session)
    ws.last_active_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)

    logger.info(
        "[sessions] Created session %s in workspace %s (%d docs)",
        session.id,
        workspace_id,
        len(req.document_ids or []),
    )

    return {
        "session_id": str(session.id),
        "workspace_id": str(workspace_id),
        "document_count": len(req.document_ids or []),
        "created_at": session.created_at.isoformat(),
    }


@router.get("/{workspace_id}/sessions/{session_id}")
async def get_workspace_session(
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a workspace session with full context:
    - Document list with statuses
    - Prior REASON findings summary
    - Session metadata
    """
    org_uuid = uuid.UUID(org_id)

    # Verify workspace access + fetch session
    result = await db.execute(
        select(WorkspaceSession).where(
            WorkspaceSession.id == session_id,
            WorkspaceSession.workspace_id == workspace_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session or session.closed_at:
        raise HTTPException(status_code=404, detail="Session not found or archived.")

    # Verify workspace belongs to org
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
        )
    )
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # Fetch document details
    docs = []
    if session.document_subset:
        doc_result = await db.execute(
            select(
                Document.id,
                Document.filename,
                Document.status,
                Document.file_type,
                Document.intelligence_stages_complete,
            ).where(Document.id.in_(session.document_subset))
        )
        for row in doc_result.all():
            docs.append(
                {
                    "document_id": str(row.id),
                    "filename": row.filename,
                    "status": row.status.value if row.status else None,
                    "file_type": row.file_type.value if row.file_type else None,
                    "stages": row.intelligence_stages_complete,
                }
            )

    # Fetch prior REASON findings (if any)
    findings = []
    if session.document_subset:
        from app.models import Finding

        find_result = await db.execute(
            select(
                Finding.claim,
                Finding.confidence,
                Finding.escalated,
                Finding.escalation_type,
            )
            .where(
                Finding.workflow_id.in_(
                    select(WorkflowExecution.id).where(
                        WorkflowExecution.session_id == session_id,
                    )
                )
            )
            .limit(10)
        )
        for row in find_result.all():
            findings.append(
                {
                    "claim": row.claim[:200],
                    "confidence": row.confidence,
                    "escalated": row.escalated,
                    "escalation_type": row.escalation_type.value
                    if row.escalation_type
                    else None,
                }
            )

    return {
        "session_id": str(session.id),
        "workspace_id": str(session.workspace_id),
        "created_at": session.created_at.isoformat(),
        "last_active_at": session.last_active_at.isoformat(),
        "document_count": len(session.document_subset or []),
        "documents": docs,
        "context": session.context,
        "prior_findings": findings,
    }


@router.patch("/{workspace_id}/sessions/{session_id}")
async def update_workspace_session(
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    req: UpdateSessionContextRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Update a session's document subset or context payload."""
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(WorkspaceSession).where(
            WorkspaceSession.id == session_id,
            WorkspaceSession.workspace_id == workspace_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session or session.closed_at:
        raise HTTPException(status_code=404, detail="Session not found or archived.")

    # Verify workspace belongs to org
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
        )
    )
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found.")

    if req.document_ids is not None:
        session.document_subset = req.document_ids
    if req.context is not None:
        session.context = req.context
    session.last_active_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "session_id": str(session.id),
        "document_count": len(session.document_subset or []),
        "updated_at": session.last_active_at.isoformat(),
    }


@router.delete("/{workspace_id}/sessions/{session_id}", status_code=204)
async def close_workspace_session(
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Close (archive) a workspace session early."""
    result = await db.execute(
        select(WorkspaceSession).where(
            WorkspaceSession.id == session_id,
            WorkspaceSession.workspace_id == workspace_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session or session.closed_at:
        raise HTTPException(
            status_code=404, detail="Session not found or already archived."
        )

    session.closed_at = datetime.now(timezone.utc)
    await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Lex SRS Consolidation — Additional Workspace Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{workspace_id}/documents/{document_id}/status")
async def get_document_status(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """
    GET /workspaces/{workspace_id}/documents/{document_id}/status

    Replaces ``GET /files/{document_id}/status`` from the legacy injest.py
    module. Returns the current document processing status, stages completed,
    and any error message.

    Tenant isolation enforced via org_id check.
    """
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.org_id == org_uuid,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    return {
        "document_id": str(doc.id),
        "filename": doc.filename,
        "status": doc.status.value if doc.status else None,
        "stages": doc.intelligence_stages_complete,
        "error": doc.error_message,
        "file_hash": doc.file_hash,
        "file_type": doc.file_type.value if doc.file_type else None,
        "upload_date": doc.upload_date.isoformat(),
    }


@router.post("/{workspace_id}/session", status_code=201)
@limiter.limit("20/minute")
async def create_workspace_scoped_session(
    request: Request,
    workspace_id: uuid.UUID,
    req: CreateSessionRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """
    POST /workspaces/{workspace_id}/session

    Creates a Redis-backed session scoped to a user-selected subset of documents.
    The session stores the org context and is automatically TTL-expired.

    Unlike the Postgres-backed ``POST /sessions`` endpoint, this is lightweight
    and designed for the chat UI's document toggle workflow.
    """
    org_uuid = uuid.UUID(org_id)

    # Verify workspace access
    ws_result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.org_id == org_uuid,
            Workspace.archived_at.is_(None),
        )
    )
    if not ws_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Workspace not found.")

    # Verify the requested documents exist and belong to this org
    doc_result = await db.execute(
        select(Document.id).where(
            Document.id.in_(req.document_ids),
            Document.workspace_id == workspace_id,
            Document.org_id == org_uuid,
        )
    )
    found_ids = {row[0] for row in doc_result.all()}
    missing_ids = [str(did) for did in req.document_ids if did not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Documents not found: {', '.join(missing_ids)}",
        )

    # Delegate to the shared Redis session helper
    session_id = await redis_create_session(
        [str(did) for did in req.document_ids], org_id, redis
    )

    return {
        "session_id": session_id,
        "workspace_id": str(workspace_id),
        "document_count": len(req.document_ids),
        "ttl_hours": 48,
    }


@router.get("/{workspace_id}/defined-terms")
async def list_defined_terms_with_conflicts(
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    conflict_only: bool = Query(
        False, alias="conflict_only", description="Filter to conflicting terms only"
    ),
):
    """
    GET /workspaces/{workspace_id}/defined-terms

    Cross-document conflict viewer. Queries the ``DefinedTermRegistry``
    for terms with conflicts in a given workspace.

    Optionally filter to only conflicting terms via ``?conflict_only=true``.
    Results are returned sorted by term name.
    """
    org_uuid = uuid.UUID(org_id)

    conditions = [
        DefinedTermRegistry.workspace_id == workspace_id,
        DefinedTermRegistry.org_id == org_uuid,
    ]
    if conflict_only:
        conditions.append(DefinedTermRegistry.conflict_flag.is_(True))

    result = await db.execute(
        select(DefinedTermRegistry)
        .where(*conditions)
        .order_by(DefinedTermRegistry.term)
    )
    terms = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "term": t.term,
            "definition": t.definition,
            "source_document_id": str(t.source_document_id),
            "page": t.page,
            "clause_reference": t.clause_reference,
            "conflict_flag": t.conflict_flag,
            "conflict_description": t.conflict_description,
        }
        for t in terms
    ]


@router.get("/{workspace_id}/deadlines")
async def list_deadlines(
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
):
    """
    GET /workspaces/{workspace_id}/deadlines

    Deadline registry viewer. Queries ``DeadlineRegistry`` for the
    workspace and returns obligations sorted by ``urgency_score``
    descending (most urgent first).

    Supports pagination with the ``limit`` query parameter.
    """
    org_uuid = uuid.UUID(org_id)

    result = await db.execute(
        select(DeadlineRegistry)
        .where(
            DeadlineRegistry.workspace_id == workspace_id,
            DeadlineRegistry.org_id == org_uuid,
        )
        .order_by(DeadlineRegistry.urgency_score.desc())
        .limit(limit)
    )
    deadlines = result.scalars().all()

    return [
        {
            "id": str(d.id),
            "obligation_description": d.obligation_description,
            "obligation_type": d.obligation_type.value,
            "raw_date_expression": d.raw_date_expression,
            "resolved_deadline": d.resolved_deadline.isoformat()
            if d.resolved_deadline
            else None,
            "resolution_status": d.resolution_status.value,
            "conflict_flag": d.conflict_flag,
            "urgency_score": d.urgency_score,
            "status": d.status.value,
        }
        for d in deadlines
    ]


@router.get("/{workspace_id}/graph/expand")
async def expand_graph(
    workspace_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
):
    """
    GET /workspaces/{workspace_id}/graph/expand

    FalkorDB graph expander (placeholder).
    Will be implemented when FalkorDB ingestion is active.

    Currently returns a descriptive placeholder indicating
    that the FalkorDB graph visualizer is not yet available.
    """
    return {
        "workspace_id": str(workspace_id),
        "status": "unavailable",
        "message": "FalkorDB graph visualization is not yet available. "
        "This endpoint will be implemented when FalkorDB ingestion is active.",
        "documentation_reference": "SRS Section 7.2 — FalkorDB Knowledge Graph",
    }


@router.post("/{workspace_id}/documents/{document_id}/reprocess", status_code=202)
async def reprocess_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Re-trigger the intelligence pipeline for a failed document."""
    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.org_id == org_uuid,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = DocumentStatus.PENDING
    doc.error_message = None
    doc.intelligence_stages_complete = None
    await db.commit()

    celery_app.send_task(
        "app.tasks.process_digital_pdf",
        kwargs={
            "document_id": str(document_id),
            "workspace_id": str(workspace_id),
            "org_id": str(org_id),
            "filename": doc.filename,
            "blob_name": doc.r2_key,
        },
    )
    return {"status": "reprocessing", "document_id": str(document_id)}


@router.delete("/{workspace_id}/documents/{document_id}", status_code=204)
async def delete_workspace_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document from a workspace."""
    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.org_id == org_uuid,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete dependent records first to avoid FK violations
    from app.models import DeadlineRegistry, DefinedTermRegistry

    await db.execute(
        delete(DefinedTermRegistry).where(
            DefinedTermRegistry.source_document_id == document_id
        )
    )
    await db.execute(
        delete(DeadlineRegistry).where(
            DeadlineRegistry.source_document_id == document_id
        )
    )

    await db.delete(doc)
    await db.commit()
