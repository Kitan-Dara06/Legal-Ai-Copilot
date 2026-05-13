"""Shared fixtures for PostgreSQL integration tests.

Connects to the real Supabase database via the DATABASE_URL in .env.
Each test gets a completely fresh connection to avoid state issues.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_database_url_async


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Create a real async SQLAlchemy engine with NullPool for fresh connections."""
    url = get_database_url_async()
    eng = create_async_engine(url, echo=False, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(engine):
    """Delete all test data before each test using CASCADE to handle FK constraints."""
    async with AsyncSession(bind=engine) as session:
        await session.execute(
            text(
                "TRUNCATE TABLE audit_log, documents, workspaces, "
                "organizations, goals, actions, approval_requests, "
                "workflow_executions, deadline_registry, defined_terms_registry CASCADE"
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def db(engine):
    """Provide a fresh AsyncSession per test with a new connection."""
    async with AsyncSession(bind=engine) as session:
        yield session


@pytest.fixture
def test_org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def test_workspace_id() -> uuid.UUID:
    return uuid.uuid4()
