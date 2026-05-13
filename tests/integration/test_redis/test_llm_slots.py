"""
Integration tests for Redis-backed LLM slot semaphore.

FR-L3-01 / Priority 1: Distributed token-bucket semaphore used to
limit concurrent LLM calls per organisation.
"""

import pytest

from app.redis_client import acquire_llm_slot, release_llm_slot

pytestmark = pytest.mark.integration


class TestLLMSlots:
    """Covers acquire, release, and capacity limits of the LLM slot semaphore."""

    @pytest.mark.asyncio
    async def test_acquire_returns_lease_id(self, redis):
        """acquire_llm_slot should return a non-empty lease_id string."""
        lease_id = await acquire_llm_slot("org_1", redis, max_slots=3)

        assert isinstance(lease_id, str)
        assert len(lease_id) > 0

    @pytest.mark.asyncio
    async def test_acquire_up_to_max_slots(self, redis):
        """Acquiring up to max_slots=3 should all succeed."""
        lease_ids = []
        for _ in range(3):
            lid = await acquire_llm_slot("org_1", redis, max_slots=3)
            lease_ids.append(lid)

        assert all(lid is not None for lid in lease_ids)
        assert len(set(lease_ids)) == 3  # All unique

    @pytest.mark.asyncio
    async def test_acquire_beyond_max_slots_returns_none(self, redis):
        """Acquiring a 4th slot when max_slots=3 should return None."""
        for _ in range(3):
            await acquire_llm_slot("org_1", redis, max_slots=3)

        overflow = await acquire_llm_slot("org_1", redis, max_slots=3)

        assert overflow is None

    @pytest.mark.asyncio
    async def test_release_slot(self, redis):
        """release_llm_slot should succeed (no exception)."""
        lease_id = await acquire_llm_slot("org_1", redis, max_slots=3)

        # Should not raise
        await release_llm_slot("org_1", lease_id, redis)

    @pytest.mark.asyncio
    async def test_acquire_after_release_succeeds(self, redis):
        """After releasing a slot, acquiring again should succeed."""
        lid1 = await acquire_llm_slot("org_1", redis, max_slots=3)
        lid2 = await acquire_llm_slot("org_1", redis, max_slots=3)
        lid3 = await acquire_llm_slot("org_1", redis, max_slots=3)

        # Release one slot
        await release_llm_slot("org_1", lid1, redis)

        # Now we should be able to acquire again
        lid4 = await acquire_llm_slot("org_1", redis, max_slots=3)
        assert lid4 is not None
        assert lid4 not in (lid1, lid2, lid3)

    @pytest.mark.asyncio
    async def test_slots_are_per_org_isolated(self, redis):
        """
        Slots should be scoped per org — acquiring for org_a should not
        affect org_b's capacity.
        """
        # Fill org_a up to max_slots=1
        await acquire_llm_slot("org_a", redis, max_slots=1)

        # org_a should be full now
        org_a_overflow = await acquire_llm_slot("org_a", redis, max_slots=1)
        assert org_a_overflow is None

        # org_b should still have capacity
        org_b_slot = await acquire_llm_slot("org_b", redis, max_slots=1)
        assert org_b_slot is not None
