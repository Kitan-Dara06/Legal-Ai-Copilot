"""
Unit tests for the three pure routing functions in the LangGraph graph module.

Tests cover:
  - _ambiguity_router: intent confidence gating and capability-path routing
  - _post_ambiguity_router: routing after human confirmation
  - _execution_router: execution loop termination and compensation routing

All three functions are pure — they read from a dict and return a string.
External module dependencies (langgraph) are mocked at import time.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# ── Mock external dependencies before importing the target module ──────────
_MOCK_GRAPH = MagicMock()
_MOCK_GRAPH.END = "__end__"
_MOCK_GRAPH.START = "__start__"
_MOCK_GRAPH.StateGraph = MagicMock()

_MOCK_MODULES = {
    "langgraph": MagicMock(),
    "langgraph.graph": _MOCK_GRAPH,
    "langgraph.graph.message": MagicMock(),
}

with patch.dict(sys.modules, _MOCK_MODULES, clear=False):
    from app.services.agent.graph import (
        _ambiguity_router,
        _execution_router,
        _post_ambiguity_router,
    )


# =============================================================================
# _ambiguity_router
# =============================================================================


class TestAmbiguityRouterLowConfidence:
    """Behaviour when intent_confidence < 0.8."""

    def test_low_confidence_not_confirmed_returns_ambiguity_gate(self):
        """confidence=0.79, not confirmed → 'ambiguity_gate'."""
        state = {"intent_confidence": 0.79, "intent_confirmed_by_human": False}
        assert _ambiguity_router(state) == "ambiguity_gate"

    def test_low_confidence_but_confirmed_returns_analyze(self):
        """confidence=0.79, confirmed by human, ANALYZE → 'analyze_path'."""
        state = {
            "intent_confidence": 0.79,
            "intent_confirmed_by_human": True,
            "primary_intent": "ANALYZE",
        }
        assert _ambiguity_router(state) == "analyze_path"


class TestAmbiguityRouterHighConfidence:
    """Routing by primary_intent when confidence >= 0.8."""

    def test_high_confidence_analyze(self):
        """confidence=0.95, primary_intent=ANALYZE → 'analyze_path'."""
        state = {"intent_confidence": 0.95, "primary_intent": "ANALYZE"}
        assert _ambiguity_router(state) == "analyze_path"

    def test_high_confidence_reason(self):
        """confidence=0.95, primary_intent=REASON → 'reason_path'."""
        state = {"intent_confidence": 0.95, "primary_intent": "REASON"}
        assert _ambiguity_router(state) == "reason_path"

    def test_high_confidence_act(self):
        """confidence=0.95, primary_intent=ACT → 'act_path'."""
        state = {"intent_confidence": 0.95, "primary_intent": "ACT"}
        assert _ambiguity_router(state) == "act_path"


class TestAmbiguityRouterEdgeCases:
    """Boundary and fallback behaviour."""

    def test_missing_confidence_defaults_to_ambiguity_gate(self):
        """Empty state defaults confidence to 0.0 → 'ambiguity_gate'."""
        assert _ambiguity_router({}) == "ambiguity_gate"

    def test_unknown_intent_falls_back_to_analyze(self):
        """Unknown primary_intent with high confidence → 'analyze_path'."""
        state = {"intent_confidence": 0.95, "primary_intent": "UNKNOWN"}
        assert _ambiguity_router(state) == "analyze_path"

    def test_boundary_at_80_not_ambiguous(self):
        """confidence=0.80 is NOT below threshold → route by intent."""
        state = {"intent_confidence": 0.80, "primary_intent": "ANALYZE"}
        assert _ambiguity_router(state) == "analyze_path"


# =============================================================================
# _post_ambiguity_router
# =============================================================================


class TestPostAmbiguityRouter:
    """Routing by primary_intent after human confirmation."""

    def test_analyze_returns_analyze_path(self):
        """primary_intent=ANALYZE → 'analyze_path'."""
        state = {"primary_intent": "ANALYZE"}
        assert _post_ambiguity_router(state) == "analyze_path"

    def test_reason_returns_reason_path(self):
        """primary_intent=REASON → 'reason_path'."""
        state = {"primary_intent": "REASON"}
        assert _post_ambiguity_router(state) == "reason_path"

    def test_act_returns_act_path(self):
        """primary_intent=ACT → 'act_path'."""
        state = {"primary_intent": "ACT"}
        assert _post_ambiguity_router(state) == "act_path"

    def test_missing_intent_falls_back_to_analyze(self):
        """Empty state → 'analyze_path' (fallback)."""
        assert _post_ambiguity_router({}) == "analyze_path"


# =============================================================================
# _execution_router
# =============================================================================


class TestExecutionRouterRecovering:
    """Behaviour when status is RECOVERING."""

    def test_recovering_status_returns_compensate(self):
        """status=RECOVERING → 'compensate'."""
        state = {"status": "RECOVERING"}
        assert _execution_router(state) == "compensate"


class TestExecutionRouterTerminal:
    """Behaviour when workflow is COMPLETED or FAILED."""

    def test_completed_status_returns_end(self):
        """status=COMPLETED → '__end__'."""
        state = {"status": "COMPLETED"}
        assert _execution_router(state) == "__end__"

    def test_failed_status_returns_end(self):
        """status=FAILED → '__end__'."""
        state = {"status": "FAILED"}
        assert _execution_router(state) == "__end__"


class TestExecutionRouterTaskProgress:
    """Routing based on current_task_index vs total_tasks."""

    def test_tasks_remain_returns_execute(self):
        """current_task_index < total_tasks → 'execute'."""
        state = {"status": "", "current_task_index": 1, "total_tasks": 5}
        assert _execution_router(state) == "execute"

    def test_all_tasks_done_returns_end(self):
        """current_task_index == total_tasks → '__end__'."""
        state = {"status": "", "current_task_index": 5, "total_tasks": 5}
        assert _execution_router(state) == "__end__"

    def test_no_tasks_returns_end(self):
        """current_task_index=0, total_tasks=0 → '__end__'."""
        state = {"status": "", "current_task_index": 0, "total_tasks": 0}
        assert _execution_router(state) == "__end__"
