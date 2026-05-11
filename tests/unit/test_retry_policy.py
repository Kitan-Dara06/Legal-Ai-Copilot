"""
Unit tests for app.services.agent.tool_registry.RetryPolicy.

Validates config storage and the exponential-backoff formula:
    delay = base_delay_s * (2 ** attempt)

Also tests the calculate_backoff helper that returns None when
attempt >= max_retries.
"""

import pytest

from app.services.agent.tool_registry import RetryPolicy


def calculate_backoff(policy: RetryPolicy, attempt: int) -> float | None:
    """
    Compute the exponential backoff delay for a given attempt.

    Returns `policy.base_delay_s * (2 ** attempt)` if `attempt < policy.max_retries`,
    otherwise `None` (the retry should not be performed).
    """
    if attempt >= policy.max_retries:
        return None
    return policy.base_delay_s * (2**attempt)


class TestRetryPolicy:
    """Suite for RetryPolicy configuration and backoff formula."""

    # ── Basic config ──────────────────────────────────────────────────

    def test_default_initialization(self):
        """Default RetryPolicy must have max_retries=3 and base_delay_s=10.0."""
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay_s == 10.0

    def test_custom_initialization(self):
        """Custom max_retries and base_delay_s must be stored correctly."""
        policy = RetryPolicy(max_retries=5, base_delay_s=2.0)
        assert policy.max_retries == 5
        assert policy.base_delay_s == 2.0

    # ── Exponential backoff math via calculate_backoff ─────────────────

    def test_attempt_zero_base_10s(self):
        """delay = 10 * 2^0 → 10.0."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 0) == 10.0

    def test_attempt_one_base_10s(self):
        """delay = 10 * 2^1 → 20.0."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 1) == 20.0

    def test_attempt_two_base_10s(self):
        """delay = 10 * 2^2 → 40.0."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 2) == 40.0

    def test_attempt_three_exceeds_max(self):
        """attempt=3 with max_retries=3 must return None (exceeds max)."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 3) is None

    def test_custom_base_30s_attempt_one(self):
        """delay = 30 * 2^1 → 60.0."""
        policy = RetryPolicy(max_retries=3, base_delay_s=30.0)
        assert calculate_backoff(policy, 1) == 60.0

    def test_max_retries_5_attempt_4(self):
        """delay = 10 * 2^4 → 160.0."""
        policy = RetryPolicy(max_retries=5, base_delay_s=10.0)
        assert calculate_backoff(policy, 4) == 160.0

    # ── Boundary cases ────────────────────────────────────────────────

    def test_last_valid_attempt_returns_delay(self):
        """The last allowed attempt (max_retries - 1) must return a valid delay."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 2) == 40.0

    def test_first_exceeding_attempt_returns_none(self):
        """The first attempt past max_retries must return None."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 3) is None

    def test_far_exceeding_attempt_returns_none(self):
        """Attempts far beyond max_retries must also return None."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert calculate_backoff(policy, 100) is None

    def test_max_retries_zero_returns_none_for_all_attempts(self):
        """With max_retries=0, every attempt must return None."""
        policy = RetryPolicy(max_retries=0, base_delay_s=10.0)
        assert calculate_backoff(policy, 0) is None
        assert calculate_backoff(policy, 1) is None

    # ── Edge cases ────────────────────────────────────────────────────

    def test_base_delay_zero(self):
        """base_delay_s=0 must yield 0 delay for valid attempts."""
        policy = RetryPolicy(max_retries=3, base_delay_s=0.0)
        assert calculate_backoff(policy, 0) == 0.0
        assert calculate_backoff(policy, 1) == 0.0
        assert calculate_backoff(policy, 2) == 0.0
        assert calculate_backoff(policy, 3) is None

    def test_base_delay_negative(self):
        """Negative base_delay is unrealistic but must not crash."""
        policy = RetryPolicy(max_retries=3, base_delay_s=-5.0)
        assert calculate_backoff(policy, 1) == -10.0

    # ── Type checks ───────────────────────────────────────────────────

    def test_base_delay_is_numeric(self):
        """The base_delay_s attribute must be numeric."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert isinstance(policy.base_delay_s, float)

    def test_max_retries_is_int(self):
        """The max_retries attribute must be an int."""
        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert isinstance(policy.max_retries, int)
