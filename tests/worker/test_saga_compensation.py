"""Tests for Saga pattern compensation (FR-EXEC-03)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.agent.nodes import ActionInfo, ToolLogEntry, build_compensation_plan


class TestCompensationPlanBuilder:
    """build_compensation_plan must return actions in LIFO order."""

    def test_empty_logs_returns_empty_plan(self):
        """No tool executions means nothing to compensate."""
        result = build_compensation_plan([], {})
        assert result == []

    def test_all_idempotent_returns_empty(self):
        """IDEMPOTENT tools don't need compensation."""
        logs = [
            ToolLogEntry(
                action_id="a1",
                created_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        actions = {
            "a1": ActionInfo(
                idempotency_class="IDEMPOTENT",
                compensation_action=None,
                compensation_params=None,
            ),
        }
        result = build_compensation_plan(logs, actions)
        assert result == []

    def test_single_requires_compensation(self):
        """A single REQUIRES_COMPENSATION action should be compensated."""
        logs = [
            ToolLogEntry(
                action_id="a1",
                created_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        actions = {
            "a1": ActionInfo(
                idempotency_class="REQUIRES_COMPENSATION",
                compensation_action="cancel_email",
                compensation_params={"message_id": "123"},
            ),
        }
        result = build_compensation_plan(logs, actions)
        assert len(result) == 1
        assert result[0] == ("a1", "cancel_email", {"message_id": "123"})

    def test_lifo_order_enforced(self):
        """Actions executed A→B→C should be compensated C→B→A."""
        logs = [
            ToolLogEntry(
                action_id="a1",
                created_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            ),
            ToolLogEntry(
                action_id="a2",
                created_at=datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc),
            ),
            ToolLogEntry(
                action_id="a3",
                created_at=datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        actions = {
            f"a{i}": ActionInfo(
                idempotency_class="REQUIRES_COMPENSATION",
                compensation_action=f"undo_{i}",
                compensation_params=None,
            )
            for i in range(1, 4)
        }

        result = build_compensation_plan(logs, actions)
        action_ids = [r[0] for r in result]
        assert action_ids == ["a3", "a2", "a1"]  # LIFO order

    def test_mixed_idempotency_filters_correctly(self):
        """Only REQUIRES_COMPENSATION actions are included."""
        logs = [
            ToolLogEntry(
                action_id="a1",
                created_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            ),
            ToolLogEntry(
                action_id="a2",
                created_at=datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        actions = {
            "a1": ActionInfo(
                idempotency_class="IDEMPOTENT",
                compensation_action=None,
                compensation_params=None,
            ),
            "a2": ActionInfo(
                idempotency_class="REQUIRES_COMPENSATION",
                compensation_action="rollback",
                compensation_params=None,
            ),
        }
        result = build_compensation_plan(logs, actions)
        assert len(result) == 1
        assert result[0][0] == "a2"
