"""
Unit tests for action sorting by urgency score descending.

These tests verify the sort-on-dicts logic independently of any
database model, using a local _sort_actions helper that replicates
the production endpoint logic.
"""

from typing import Any


def _sort_actions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replicates the production sort: urgency_score descending, None → 0.0."""
    return sorted(
        items,
        key=lambda x: x.get("urgency") if x.get("urgency") is not None else 0.0,
        reverse=True,
    )


class TestActionSortingOrder:
    """Verify descending sort by urgency score."""

    def test_sorts_high_urgency_first(self):
        """[0.3, 0.9, 0.5] sorts as [0.9, 0.5, 0.3]."""
        items = [
            {"urgency": 0.3},
            {"urgency": 0.9},
            {"urgency": 0.5},
        ]
        sorted_items = _sort_actions(items)
        scores = [item["urgency"] for item in sorted_items]
        assert scores == [0.9, 0.5, 0.3]

    def test_none_treated_as_zero(self):
        """None urgency sorts after any numeric value."""
        items = [
            {"urgency": None},
            {"urgency": 0.5},
        ]
        sorted_items = _sort_actions(items)
        assert sorted_items[0]["urgency"] == 0.5
        assert sorted_items[1]["urgency"] is None

    def test_stable_sort_preserves_order(self):
        """Equal urgency values preserve original order."""
        items = [
            {"urgency": 0.5, "id": "first"},
            {"urgency": 0.5, "id": "second"},
            {"urgency": 0.5, "id": "third"},
        ]
        sorted_items = _sort_actions(items)
        ids = [item["id"] for item in sorted_items]
        assert ids == ["first", "second", "third"]

    def test_empty_list(self):
        """Empty list returns empty list."""
        assert _sort_actions([]) == []

    def test_single_item(self):
        """Single item list returns that item."""
        items = [{"urgency": 0.7}]
        assert _sort_actions(items) == [{"urgency": 0.7}]
