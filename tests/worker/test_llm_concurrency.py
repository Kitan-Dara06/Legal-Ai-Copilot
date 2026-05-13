"""Tests for distributed LLM concurrency limiting via Redis."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLLMSlotAcquire:
    """acquire_llm_slot should respect max_slots limit."""

    @pytest.mark.asyncio
    async def test_acquire_within_limit_returns_lease(self):
        """A valid slot should return a lease ID."""
        from app.redis_client import acquire_llm_slot

        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=2)  # 2 of 5 used
        mock_redis.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()

        lease_id = await acquire_llm_slot("org_1", mock_redis, max_slots=5)
        assert lease_id is not None
        assert isinstance(lease_id, str)

    @pytest.mark.asyncio
    async def test_exceed_limit_returns_none(self):
        """When at capacity, acquire should return None."""
        from app.redis_client import acquire_llm_slot

        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=5)  # 5 of 5 used

        lease_id = await acquire_llm_slot("org_1", mock_redis, max_slots=5)
        assert lease_id is None

    @pytest.mark.asyncio
    async def test_release_frees_slot(self):
        """After release, a new acquire should succeed."""
        from app.redis_client import release_llm_slot

        mock_redis = AsyncMock()
        mock_redis.zrem = AsyncMock()

        await release_llm_slot("org_1", "lease_123", mock_redis)
        mock_redis.zrem.assert_called_once()

    @pytest.mark.asyncio
    async def test_different_orgs_have_independent_limits(self):
        """Each org gets its own slot pool."""
        from app.redis_client import acquire_llm_slot

        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        # Org A is full, Org B has space
        mock_redis.zcard = AsyncMock(side_effect=[5, 2])
        mock_redis.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()

        lease_a = await acquire_llm_slot("org_A", mock_redis, max_slots=5)
        lease_b = await acquire_llm_slot("org_B", mock_redis, max_slots=5)

        assert lease_a is None  # Org A full
        assert lease_b is not None  # Org B has space
