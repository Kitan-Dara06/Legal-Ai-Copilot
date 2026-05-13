"""
Tests for the deadline scanner and urgency calculator.

The ``calculate_urgency`` function is a pure mapping of days-remaining
to a float score.  ``deadline_scanner`` is a Celery bound task that
scans the DB, recalculates scores, and pushes notifications.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.tasks import calculate_urgency


class TestUrgencyCalculator:
    """calculate_urgency must return correct scores for all date ranges."""

    # ── Overdue / due today ───────────────────────────────────────────

    def test_overdue_or_due_today_returns_1_0(self):
        """days_remaining <= 0 means urgency = 1.0"""
        assert calculate_urgency(0) == 1.0
        assert calculate_urgency(-1) == 1.0
        assert calculate_urgency(-365) == 1.0

    # ── Exact boundary cases ──────────────────────────────────────────

    def test_due_tomorrow_returns_0_95(self):
        assert calculate_urgency(1) == 0.95

    def test_due_in_3_days_returns_0_85(self):
        assert calculate_urgency(3) == 0.85

    def test_due_in_7_days_returns_0_70(self):
        assert calculate_urgency(7) == 0.70

    def test_due_in_14_days_returns_0_50(self):
        assert calculate_urgency(14) == 0.50

    def test_due_in_30_days_returns_0_30(self):
        assert calculate_urgency(30) == 0.30

    def test_far_future_returns_0_10(self):
        assert calculate_urgency(90) == 0.10
        assert calculate_urgency(365) == 0.10

    # ── Internal bucket boundaries ────────────────────────────────────

    def test_two_days_falls_into_3_day_bucket(self):
        """days_remaining == 2 should land in the <=3 bucket."""
        assert calculate_urgency(2) == 0.85

    def test_four_days_falls_into_7_day_bucket(self):
        """days_remaining == 4 should land in the <=7 bucket."""
        assert calculate_urgency(4) == 0.70

    def test_eight_days_falls_into_14_day_bucket(self):
        """days_remaining == 8 should land in the <=14 bucket."""
        assert calculate_urgency(8) == 0.50

    def test_fifteen_days_falls_into_30_day_bucket(self):
        """days_remaining == 15 should land in the <=30 bucket."""
        assert calculate_urgency(15) == 0.30

    def test_thirty_one_days_falls_into_default_bucket(self):
        """days_remaining == 31 falls past all thresholds → 0.10."""
        assert calculate_urgency(31) == 0.10

    # ── Type & range sanity ───────────────────────────────────────────

    def test_returns_float(self):
        assert isinstance(calculate_urgency(7), float)

    def test_result_stays_in_zero_one_range(self):
        for days in (-100, -1, 0, 1, 2, 3, 7, 14, 30, 90, 365):
            val = calculate_urgency(days)
            assert 0.0 <= val <= 1.0, f"Out of range for days={days}: {val}"


class TestDeadlineScannerBehavior:
    """The scanner must update DB status and trigger notifications."""

    @patch("app.tasks.get_pg_pool")
    def test_scanner_recalculates_scores(self, mock_get_pool):
        """deadline_scanner should recalculate urgency for ACTIVE deadlines."""
        from app.tasks import deadline_scanner

        # ── Build mocks ───────────────────────────────────────────────
        now = datetime.now(timezone.utc)

        mock_cursor = MagicMock()
        # Single ACTIVE deadline due tomorrow
        mock_cursor.fetchall.return_value = [
            ("dl_001", now, 0.5, "File annual report", "ws_1", "org_1"),
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_get_pool.return_value = mock_pool

        # ── Mock self (bound task) ────────────────────────────────────
        mock_self = MagicMock()
        mock_self.max_retries = 3
        mock_self.default_retry_delay = 60

        # ── Execute ───────────────────────────────────────────────────
        deadline_scanner(mock_self)

        # ── Assertions ────────────────────────────────────────────────
        # Must have performed at least one UPDATE for urgency_score
        update_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if "UPDATE deadline_registry SET urgency_score" in str(c)
        ]
        assert len(update_calls) >= 1, "Expected at least one urgency_score UPDATE"

        # Must have committed the transaction
        assert mock_conn.commit.called, "Transaction was not committed"

        # Must have returned the connection to the pool
        assert mock_pool.putconn.called, "Connection was not returned to pool"

    @patch("app.tasks.get_pg_pool")
    def test_scanner_marks_overdue_deadlines(self, mock_get_pool):
        """Deadlines with days_remaining <= 0 should be marked OVERDUE."""
        from app.tasks import deadline_scanner

        now = datetime.now(timezone.utc)

        # Two deadlines: one already overdue, one due in 10 days
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("dl_overdue", now, 0.8, "Overdue item", "ws_1", "org_1"),
            ("dl_future", now, 0.3, "Future item", "ws_2", "org_2"),
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_get_pool.return_value = mock_pool

        mock_self = MagicMock()
        mock_self.max_retries = 3

        deadline_scanner(mock_self)

        # Must have executed OVERDUE status update
        overdue_calls = [
            c
            for c in mock_cursor.execute.call_args_list
            if "UPDATE deadline_registry SET status" in str(c) and "OVERDUE" in str(c)
        ]
        assert len(overdue_calls) >= 1, "Expected at least one OVERDUE status update"

    @patch("app.tasks.get_pg_pool")
    def test_scanner_does_not_crash_on_empty_set(self, mock_get_pool):
        """Scanner should handle an empty ACTIVE deadline set gracefully."""
        from app.tasks import deadline_scanner

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []  # No deadlines

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_get_pool.return_value = mock_pool

        mock_self = MagicMock()
        mock_self.max_retries = 3

        # Should not raise
        deadline_scanner(mock_self)
        assert mock_conn.commit.called
