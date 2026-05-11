"""
Unit tests for the build_compensation_plan function (Saga pattern).

The function takes a chronological list of tool call log entries and an
actions_map, then returns compensatable actions in LIFO order.  All
inputs are plain dataclass instances — no database or I/O required.
"""

from datetime import datetime, timezone

import pytest

from app.services.agent.nodes import ActionInfo, ToolLogEntry, build_compensation_plan


def _log(action_id: str, created_at: datetime | None = None) -> ToolLogEntry:
    """Helper: create a ToolLogEntry with an optional timestamp."""
    return ToolLogEntry(
        action_id=action_id,
        created_at=created_at or datetime.now(tz=timezone.utc),
    )


def _action(
    idempotency_class: str,
    compensation_action: str | None = None,
    compensation_params: dict | None = None,
) -> ActionInfo:
    """Helper: create an ActionInfo with the given idempotency metadata."""
    return ActionInfo(
        idempotency_class=idempotency_class,
        compensation_action=compensation_action,
        compensation_params=compensation_params,
    )


# re-usable idempotency class values as plain strings (matches .value)
IDEMPOTENT = "IDEMPOTENT"
REQUIRES_COMPENSATION = "REQUIRES_COMPENSATION"
NON_IDEMPOTENT = "NON_IDEMPOTENT"


class TestCompensationPlanNoActions:
    """Edge cases where nothing should be compensated."""

    def test_all_idempotent_actions_returns_empty(self):
        """All actions are IDEMPOTENT → empty plan."""
        logs = [
            _log("action-1", datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)),
            _log("action-2", datetime(2024, 1, 1, 2, 0, 0, tzinfo=timezone.utc)),
        ]
        actions_map = {
            "action-1": _action(IDEMPOTENT),
            "action-2": _action(IDEMPOTENT),
        }
        plan = build_compensation_plan(logs, actions_map)
        assert plan == []

    def test_empty_logs_returns_empty(self):
        """No tool logs → empty plan."""
        plan = build_compensation_plan([], {})
        assert plan == []


class TestCompensationPlanSingle:
    """Single action that requires compensation."""

    def test_single_requires_compensation(self):
        """One REQUIRES_COMPENSATION action → returned in plan."""
        logs = [
            _log("action-1", datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)),
        ]
        actions_map = {
            "action-1": _action(
                REQUIRES_COMPENSATION,
                compensation_action="rollback_action",
            ),
        }
        plan = build_compensation_plan(logs, actions_map)
        assert len(plan) == 1
        assert plan[0][0] == "action-1"
        assert plan[0][1] == "rollback_action"


class TestCompensationPlanLifoOrder:
    """Multiple compensatable actions returned in reverse execution order."""

    def test_lifo_order(self):
        """Actions A→B→C executed → plan returns [C, B, A]."""
        logs = [
            _log("action-a", datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)),
            _log("action-b", datetime(2024, 1, 1, 2, 0, 0, tzinfo=timezone.utc)),
            _log("action-c", datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)),
        ]
        actions_map = {
            "action-a": _action(
                REQUIRES_COMPENSATION,
                compensation_action="comp_a",
            ),
            "action-b": _action(
                REQUIRES_COMPENSATION,
                compensation_action="comp_b",
            ),
            "action-c": _action(
                REQUIRES_COMPENSATION,
                compensation_action="comp_c",
            ),
        }
        plan = build_compensation_plan(logs, actions_map)
        assert [entry[0] for entry in plan] == ["action-c", "action-b", "action-a"]


class TestCompensationPlanMixed:
    """Mix of idempotency classes — only REQUIRES_COMPENSATION is returned."""

    def test_only_requires_compensation_returned(self):
        """[IDEMPOTENT, REQUIRES_COMP, NON_IDEMPOTENT] → only the middle one."""
        logs = [
            _log("action-1", datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)),
            _log("action-2", datetime(2024, 1, 1, 2, 0, 0, tzinfo=timezone.utc)),
            _log("action-3", datetime(2024, 1, 1, 3, 0, 0, tzinfo=timezone.utc)),
        ]
        actions_map = {
            "action-1": _action(IDEMPOTENT),
            "action-2": _action(
                REQUIRES_COMPENSATION,
                compensation_action="rollback",
            ),
            "action-3": _action(NON_IDEMPOTENT),
        }
        plan = build_compensation_plan(logs, actions_map)
        assert len(plan) == 1
        assert plan[0][0] == "action-2"


class TestCompensationPlanMissingAction:
    """Log entry referencing an action not in actions_map."""

    def test_missing_action_skipped(self):
        """Log references action not in map → silently skip."""
        logs = [
            _log("action-1", datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)),
            _log("missing-action", datetime(2024, 1, 1, 2, 0, 0, tzinfo=timezone.utc)),
        ]
        actions_map = {
            "action-1": _action(
                REQUIRES_COMPENSATION,
                compensation_action="comp_action_1",
            ),
        }
        plan = build_compensation_plan(logs, actions_map)
        assert len(plan) == 1
        assert plan[0][0] == "action-1"


class TestCompensationPlanParams:
    """Compensation params are preserved in output."""

    def test_compensation_params_preserved(self):
        """Action has compensation_params → included in tuple."""
        logs = [
            _log("action-1", datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)),
        ]
        actions_map = {
            "action-1": _action(
                REQUIRES_COMPENSATION,
                compensation_action="undo_action",
                compensation_params={"reason": "rollback required"},
            ),
        }
        plan = build_compensation_plan(logs, actions_map)
        assert len(plan) == 1
        action_id, comp_action, comp_params = plan[0]
        assert action_id == "action-1"
        assert comp_action == "undo_action"
        assert comp_params == {"reason": "rollback required"}
