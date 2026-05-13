"""
Unit tests for app.tasks.calculate_urgency.

Tests the pure function that maps days_remaining → urgency score in [0.0, 1.0].
Logic (from source):
    <= 0  → 1.0
    == 1  → 0.95
    <= 3  → 0.85
    <= 7  → 0.70
    <= 14 → 0.50
    <= 30 → 0.30
    else  → 0.10
"""

import pytest

from app.tasks import calculate_urgency


class TestCalculateUrgency:
    """Suite for calculate_urgency — pure function, no I/O needed."""

    # ── Overdue / past-due cases ──────────────────────────────────────

    def test_zero_days_returns_max_urgency(self):
        """days_remaining == 0 (due today) must return 1.0."""
        assert calculate_urgency(0) == 1.0

    def test_negative_days_returns_max_urgency(self):
        """days_remaining == -5 (overdue) must return 1.0."""
        assert calculate_urgency(-5) == 1.0

    def test_large_negative_days_returns_max_urgency(self):
        """days_remaining == -365 (far overdue) must return 1.0."""
        assert calculate_urgency(-365) == 1.0

    # ── Exact-day boundary cases ──────────────────────────────────────

    def test_one_day_remaining(self):
        """days_remaining == 1 must return 0.95."""
        assert calculate_urgency(1) == 0.95

    def test_three_days_remaining(self):
        """days_remaining == 3 must return 0.85 (<=3 bucket)."""
        assert calculate_urgency(3) == 0.85

    def test_five_days_remaining(self):
        """days_remaining == 5 must return 0.70 (<=7 bucket)."""
        assert calculate_urgency(5) == 0.70

    def test_seven_days_remaining(self):
        """days_remaining == 7 must return 0.70 (<=7 bucket)."""
        assert calculate_urgency(7) == 0.70

    def test_fourteen_days_remaining(self):
        """days_remaining == 14 must return 0.50 (<=14 bucket)."""
        assert calculate_urgency(14) == 0.50

    def test_thirty_days_remaining(self):
        """days_remaining == 30 must return 0.30 (<=30 bucket)."""
        assert calculate_urgency(30) == 0.30

    # ── Default (else) branch ─────────────────────────────────────────

    def test_over_thirty_days_returns_default_low(self):
        """days_remaining == 90 (beyond all thresholds) must return 0.10."""
        assert calculate_urgency(90) == 0.10

    def test_very_far_deadline_returns_default_low(self):
        """days_remaining == 365 must return 0.10."""
        assert calculate_urgency(365) == 0.10

    # ── Internal bucket boundaries ────────────────────────────────────

    def test_two_days_falls_into_three_day_bucket(self):
        """days_remaining == 2 must return 0.85 (<=3)."""
        assert calculate_urgency(2) == 0.85

    def test_four_days_falls_into_seven_day_bucket(self):
        """days_remaining == 4 must return 0.70 (<=7)."""
        assert calculate_urgency(4) == 0.70

    def test_eight_days_falls_into_fourteen_day_bucket(self):
        """days_remaining == 8 must return 0.50 (<=14)."""
        assert calculate_urgency(8) == 0.50

    def test_fifteen_days_falls_into_thirty_day_bucket(self):
        """days_remaining == 15 must return 0.30 (<=30)."""
        assert calculate_urgency(15) == 0.30

    # ── Type and range sanity ─────────────────────────────────────────

    def test_returns_float(self):
        """The return value must be a float."""
        result = calculate_urgency(7)
        assert isinstance(result, float)

    def test_result_stays_in_zero_one_range(self):
        """All results must be within [0.0, 1.0]."""
        for days in [-100, -1, 0, 1, 2, 3, 4, 5, 7, 8, 14, 15, 30, 31, 90, 365]:
            val = calculate_urgency(days)
            assert 0.0 <= val <= 1.0, f"Out of range for days={days}: {val}"
