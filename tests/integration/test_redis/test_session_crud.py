"""
Integration tests for Redis-based session CRUD operations.

FR-L3-01 / Priority 1: Session lifecycle — create, read, update, and
non-existent lookups against the Redis-backed session store.
"""

import uuid

import pytest

from app.redis_client import (
    add_file_to_session,
    create_session,
    get_session,
)

pytestmark = pytest.mark.integration


class TestSessionCRUD:
    """Covers the full lifecycle of a Redis session."""

    @pytest.mark.asyncio
    async def test_create_session_returns_valid_uuid(self, redis):
        """create_session should return a valid string UUID."""
        session_id = await create_session([1, 2, 3], "org_1", redis)

        assert isinstance(session_id, str)
        # Verify it's a well-formed UUID v4
        parsed = uuid.UUID(session_id)
        assert parsed.version == 4

    @pytest.mark.asyncio
    async def test_get_session_returns_org_id_and_files(self, redis):
        """get_session for an existing session should return org_id and files."""
        session_id = await create_session([1, 2, 3], "org_1", redis)

        result = await get_session(session_id, redis)

        assert result is not None
        assert result["org_id"] == "org_1"
        assert isinstance(result["files"], dict)
        assert result["files"] == {1: "READY", 2: "READY", 3: "READY"}

    @pytest.mark.asyncio
    async def test_get_session_nonexistent_returns_none(self, redis):
        """get_session for a non-existent session should return None."""
        result = await get_session("nonexistent", redis)

        assert result is None

    @pytest.mark.asyncio
    async def test_add_file_to_session_returns_true(self, redis):
        """add_file_to_session should return True when the session exists."""
        session_id = await create_session([1, 2, 3], "org_1", redis)

        ok = await add_file_to_session(session_id, 4, redis)

        assert ok is True

        # Verify the file was actually added
        session = await get_session(session_id, redis)
        assert session is not None
        assert 4 in session["files"]

    @pytest.mark.asyncio
    async def test_add_file_to_session_nonexistent_returns_false(self, redis):
        """add_file_to_session for a non-existent session should return False."""
        fake_id = str(uuid.uuid4())
        ok = await add_file_to_session(fake_id, 99, redis)

        assert ok is False

    @pytest.mark.asyncio
    async def test_session_contains_expected_files_after_creation(self, redis):
        """The session dict should contain exactly the files passed at creation."""
        session_id = await create_session([10, 20, 30], "org_2", redis)

        session = await get_session(session_id, redis)

        assert set(session["files"].keys()) == {10, 20, 30}
        assert all(v == "READY" for v in session["files"].values())
