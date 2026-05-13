"""Integration tests for tenant isolation between organisations.

Tests that:
1. An Organisation + Workspace can be created and persisted.
2. Querying workspaces by the correct org_id returns the workspace.
3. Querying workspaces by a different org_id returns no rows.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import IntelligenceStatus, Organization, Workspace

pytestmark = pytest.mark.integration


@pytest.fixture
def alien_org_id() -> uuid.UUID:
    """A different organisation UUID that should never match the test org."""
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_create_org_and_workspace(db, test_org_id, test_workspace_id) -> None:
    """Create an Organisation and a Workspace, then verify they exist."""
    # ── Arrange ──────────────────────────────────────────────────────────
    org = Organization(
        id=test_org_id,
        slug=f"test-org-{test_org_id}",
        name="Test Organisation",
    )
    ws = Workspace(
        id=test_workspace_id,
        org_id=test_org_id,
        name="Test Workspace",
        description="Integration test workspace",
        intelligence_status=IntelligenceStatus.PENDING,
    )
    db.add(org)
    db.add(ws)
    await db.flush()  # make visible within this transaction

    # ── Act ──────────────────────────────────────────────────────────────
    result_org = await db.get(Organization, test_org_id)
    result_ws = await db.get(Workspace, test_workspace_id)

    # ── Assert ───────────────────────────────────────────────────────────
    assert result_org is not None
    assert result_org.slug == f"test-org-{test_org_id}"
    assert result_org.name == "Test Organisation"
    assert isinstance(result_org.created_at, datetime)

    assert result_ws is not None
    assert result_ws.org_id == test_org_id
    assert result_ws.name == "Test Workspace"
    assert result_ws.intelligence_status == IntelligenceStatus.PENDING
    # Defaults
    assert result_ws.document_count == 0
    assert isinstance(result_ws.created_at, datetime)
    assert isinstance(result_ws.last_active_at, datetime)


@pytest.mark.asyncio
async def test_query_workspace_by_org_id(db, test_org_id, test_workspace_id) -> None:
    """Query workspaces filtered by org_id — should return the row."""
    # ── Arrange ──────────────────────────────────────────────────────────
    org = Organization(
        id=test_org_id,
        slug=f"test-org-{test_org_id}",
        name="Test Organisation",
    )
    ws = Workspace(
        id=test_workspace_id,
        org_id=test_org_id,
        name="Query-by-org WS",
        intelligence_status=IntelligenceStatus.PENDING,
    )
    db.add(org)
    db.add(ws)
    await db.flush()

    # ── Act ──────────────────────────────────────────────────────────────
    stmt = select(Workspace).where(Workspace.org_id == test_org_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # ── Assert ───────────────────────────────────────────────────────────
    assert len(rows) == 1
    assert rows[0].id == test_workspace_id
    assert rows[0].org_id == test_org_id


@pytest.mark.asyncio
async def test_tenant_isolation_no_leak(db, test_org_id, alien_org_id) -> None:
    """Querying with a foreign org_id returns no rows (no data leakage)."""
    # ── Arrange ──────────────────────────────────────────────────────────
    # Only create data under test_org_id, not alien_org_id
    org = Organization(
        id=test_org_id,
        slug=f"test-org-{test_org_id}",
        name="Test Organisation",
    )
    ws = Workspace(
        id=uuid.uuid4(),
        org_id=test_org_id,
        name="Isolation WS",
        intelligence_status=IntelligenceStatus.PENDING,
    )
    db.add(org)
    db.add(ws)
    await db.flush()

    # ── Act — query with a completely different org_id ───────────────────
    stmt = select(Workspace).where(Workspace.org_id == alien_org_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # ── Assert ───────────────────────────────────────────────────────────
    assert len(rows) == 0, (
        f"Expected zero workspaces for alien org {alien_org_id}, got {len(rows)}"
    )
