"""Integration tests for document deduplication via file_hash.

Tests that:
1. A document with a known file_hash can be inserted and retrieved.
2. A second document with a different file_hash exists independently.
3. Both documents co-exist without conflict.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import (
    Document,
    DocumentStatus,
    DocumentType,
    IntelligenceStatus,
    Organization,
    Workspace,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def test_org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def test_workspace_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def test_doc_id_a() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def test_doc_id_b() -> uuid.UUID:
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_insert_and_query_by_file_hash(
    db, test_org_id, test_workspace_id, test_doc_id_a
) -> None:
    """Insert a document with file_hash='abc123' and retrieve it by hash."""
    # ── Arrange ──────────────────────────────────────────────────────────
    org = Organization(
        id=test_org_id,
        slug=f"dedup-org-{test_org_id}",
        name="Dedup Test Org",
    )
    ws = Workspace(
        id=test_workspace_id,
        org_id=test_org_id,
        name="Dedup WS",
        intelligence_status=IntelligenceStatus.PENDING,
    )
    db.add(org)
    db.add(ws)
    await db.flush()

    doc = Document(
        id=test_doc_id_a,
        workspace_id=test_workspace_id,
        org_id=test_org_id,
        filename="report_a.pdf",
        file_hash="abc123",
        r2_key="uploads/report_a.pdf",
        status=DocumentStatus.READY,
    )
    db.add(doc)
    await db.flush()

    # ── Act ──────────────────────────────────────────────────────────────
    stmt = select(Document).where(Document.file_hash == "abc123")
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # ── Assert ───────────────────────────────────────────────────────────
    assert len(rows) == 1
    assert rows[0].id == test_doc_id_a
    assert rows[0].file_hash == "abc123"
    assert rows[0].status == DocumentStatus.READY
    assert rows[0].filename == "report_a.pdf"


@pytest.mark.asyncio
async def test_multiple_docs_independent_hashes(
    db, test_org_id, test_workspace_id, test_doc_id_a, test_doc_id_b
) -> None:
    """Insert two documents with different hashes; verify both exist."""
    # ── Arrange ──────────────────────────────────────────────────────────
    org = Organization(
        id=test_org_id,
        slug=f"dedup-org-{test_org_id}",
        name="Dedup Test Org",
    )
    ws = Workspace(
        id=test_workspace_id,
        org_id=test_org_id,
        name="Dedup WS",
        intelligence_status=IntelligenceStatus.PENDING,
    )
    db.add(org)
    db.add(ws)
    await db.flush()

    doc_a = Document(
        id=test_doc_id_a,
        workspace_id=test_workspace_id,
        org_id=test_org_id,
        filename="report_a.pdf",
        file_hash="abc123",
        r2_key="uploads/report_a.pdf",
        status=DocumentStatus.READY,
    )
    doc_b = Document(
        id=test_doc_id_b,
        workspace_id=test_workspace_id,
        org_id=test_org_id,
        filename="report_b.pdf",
        file_hash="def456",
        r2_key="uploads/report_b.pdf",
        status=DocumentStatus.PENDING,
    )
    db.add(doc_a)
    db.add(doc_b)
    await db.flush()

    # ── Act — query each hash independently ──────────────────────────────
    stmt_a = select(Document).where(Document.file_hash == "abc123")
    stmt_b = select(Document).where(Document.file_hash == "def456")

    result_a = await db.execute(stmt_a)
    result_b = await db.execute(stmt_b)

    rows_a = result_a.scalars().all()
    rows_b = result_b.scalars().all()

    # ── Assert ───────────────────────────────────────────────────────────
    assert len(rows_a) == 1
    assert rows_a[0].id == test_doc_id_a
    assert rows_a[0].file_hash == "abc123"
    assert rows_a[0].status == DocumentStatus.READY

    assert len(rows_b) == 1
    assert rows_b[0].id == test_doc_id_b
    assert rows_b[0].file_hash == "def456"
    assert rows_b[0].status == DocumentStatus.PENDING

    # Verify counts
    stmt_all = select(Document)
    result_all = await db.execute(stmt_all)
    all_docs = result_all.scalars().all()
    assert len(all_docs) == 2
