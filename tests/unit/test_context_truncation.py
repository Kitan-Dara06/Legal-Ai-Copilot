"""
Unit tests for app.services.store._deduplicate_by_parent.

Tests hierarchical deduplication and context truncation logic.
All I/O is mocked; no external dependencies (no Qdrant, no Cohere).
"""

import pytest

from app.services.store import _deduplicate_by_parent


def _make_result(
    text: str,
    score: float,
    parent_id: str = "",
    section_context: str = "",
    source: str = "TestContract",
    page: int = 1,
    result_id: str = "",
) -> dict:
    """Helper to build a result dict matching the search_hybrid output shape."""
    return {
        "id": result_id or text,
        "text": text,
        "score": score,
        "metadata": {
            "source": source,
            "page": page,
            "parent_id": parent_id,
            "section_context": section_context,
            "file_id": 1,
            "org_id": "test_org",
        },
    }


class TestDeduplicateByParent:
    """Tests for the _deduplicate_by_parent function."""

    def test_empty_list_returns_empty_list(self):
        """Empty input should return an empty list."""
        result = _deduplicate_by_parent([], max_context_chars=12000)
        assert result == []

    def test_single_result_no_parent_id_returned_as_is(self):
        """
        A single result with no parent_id should be treated as
        standalone and returned unchanged.
        """
        results = [_make_result("Some text.", score=0.9)]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 1
        assert output[0]["text"] == "Some text."
        assert output[0]["score"] == 0.9
        assert output[0]["is_parent"] is False

    def test_two_results_same_parent_id_deduplicated(self):
        """
        Two results sharing the same parent_id should collapse into
        a single parent entry containing the section context text.
        """
        parent_text = "Parent section context here."
        results = [
            _make_result(
                text="Child one content.",
                score=0.8,
                parent_id="p1",
                section_context=parent_text,
            ),
            _make_result(
                text="Child two content.",
                score=0.6,
                parent_id="p1",
                section_context=parent_text,
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 1
        # The parent text should contain the section context
        assert parent_text in output[0]["text"]
        # Score should be first child's score + 50% of second child's score
        assert output[0]["score"] == pytest.approx(0.8 + 0.6 * 0.5)
        assert output[0]["matched_children"] == 2
        assert output[0]["is_parent"] is True

    def test_results_with_different_parent_ids_both_returned(self):
        """
        Two results with different parent_ids should each produce
        a separate parent entry.
        """
        results = [
            _make_result(
                text="Child A.",
                score=0.7,
                parent_id="p1",
                section_context="Section 1 context.",
            ),
            _make_result(
                text="Child B.",
                score=0.5,
                parent_id="p2",
                section_context="Section 2 context.",
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 2
        # Both parent texts should be present
        texts = [d["text"] for d in output]
        assert any("Section 1 context" in t for t in texts)
        assert any("Section 2 context" in t for t in texts)

    def test_truncation_drops_lowest_scoring_when_over_limit(self):
        """
        When results exceed max_context_chars, the lowest-scoring
        entries should be dropped.
        """
        # Create results with large section_context to force truncation
        results = [
            _make_result(
                text="High score.",
                score=1.0,
                parent_id="p_high",
                section_context="A" * 5000,
            ),
            _make_result(
                text="Medium score.",
                score=0.5,
                parent_id="p_medium",
                section_context="B" * 5000,
            ),
            _make_result(
                text="Low score.",
                score=0.1,
                parent_id="p_low",
                section_context="C" * 5000,
            ),
        ]
        # Set max_context_chars so only the top two fit
        output = _deduplicate_by_parent(results, max_context_chars=11000)
        # Sorted by score descending: high then medium -> ~10000 chars, low is cut
        assert len(output) == 2
        assert (
            "p_high" in output[0]["metadata"]["parent_id"]
            or "p_high" in output[1]["metadata"]["parent_id"]
        )
        assert "p_low" not in [d["metadata"]["parent_id"] for d in output]

    def test_section_context_empty_treated_as_standalone(self):
        """
        A result with a non-empty parent_id but empty section_context
        should be treated as a standalone child (not grouped).
        """
        results = [
            _make_result(
                text="Standalone child.",
                score=0.9,
                parent_id="orphan_parent",
                section_context="",
            ),
            _make_result(
                text="Another standalone.",
                score=0.8,
                parent_id="orphan_parent",
                section_context="",
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        # Both should be standalone since section_context is empty
        assert len(output) == 2
        assert all(d["is_parent"] is False for d in output)

    def test_section_context_none_treated_as_standalone(self):
        """
        A result with a non-empty parent_id but None section_context
        should be treated as a standalone child.
        """
        results = [
            {
                "id": "r1",
                "text": "Standalone.",
                "score": 0.8,
                "metadata": {
                    "source": "Test",
                    "page": 1,
                    "parent_id": "orphan",
                    "section_context": None,
                },
            }
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 1
        assert output[0]["is_parent"] is False

    def test_score_accumulation_with_multiple_children(self):
        """
        Each additional child sharing a parent_id adds 50% of its
        score to the parent's aggregated score.
        """
        parent_text = "Parent context."
        results = [
            _make_result(
                text="C1", score=1.0, parent_id="p1", section_context=parent_text
            ),
            _make_result(
                text="C2", score=0.8, parent_id="p1", section_context=parent_text
            ),
            _make_result(
                text="C3", score=0.4, parent_id="p1", section_context=parent_text
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 1
        # Expected: 1.0 + (0.8 * 0.5) + (0.4 * 0.5) = 1.0 + 0.4 + 0.2 = 1.6
        assert output[0]["score"] == pytest.approx(1.6)
        assert output[0]["matched_children"] == 3

    def test_mixed_standalone_and_parented_results(self):
        """
        A mix of standalone results and parent-grouped results should
        all be preserved in the output.
        """
        results = [
            _make_result(
                text="Standalone doc.",
                score=0.9,
                parent_id="",
                section_context="",
            ),
            _make_result(
                text="Parented child.",
                score=0.7,
                parent_id="gp",
                section_context="Group context.",
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 2
        standalone_entries = [d for d in output if d["is_parent"] is False]
        parent_entries = [d for d in output if d["is_parent"] is True]
        assert len(standalone_entries) == 1
        assert len(parent_entries) == 1

    def test_under_budget_all_chunks_returned(self):
        """
        When total text is under max_context_chars, all chunks should be
        returned without truncation.
        """
        chunks = [
            _make_result(text="A" * 1000, score=0.9),
            _make_result(text="B" * 2000, score=0.8),
            _make_result(text="C" * 2000, score=0.7),
        ]
        # 1000 + 2000 + 2000 = 5000, well under 12000
        output = _deduplicate_by_parent(chunks, max_context_chars=12000)
        assert len(output) == 3

    def test_single_chunk_over_budget_dropped(self):
        """
        A single chunk exceeding max_context_chars is dropped because
        the function cannot split it and strictly enforces the budget.
        """
        massive_text = "X" * 15000
        chunks = [_make_result(text=massive_text, score=0.9)]
        output = _deduplicate_by_parent(chunks, max_context_chars=12000)
        # The chunk exceeds budget, so it gets dropped entirely
        assert output == []

    def test_under_budget_with_parent_groups(self):
        """
        When parent-grouped results stay under the character budget, all
        groups should be returned.
        """
        results = [
            _make_result(
                text="Child A",
                score=0.9,
                parent_id="p1",
                section_context="Small ctx.",
            ),
            _make_result(
                text="Child B",
                score=0.5,
                parent_id="p2",
                section_context="Tiny.",
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 2

    def test_over_budget_with_ten_chunks_truncated(self):
        """
        Ten chunks totalling ~20000 chars should be truncated to stay
        under max_context_chars, preserving highest-scoring entries first.
        """
        chunks = [
            _make_result(text="C" + str(i), score=round(1.0 - i * 0.1, 1))
            for i in range(10)
        ]
        # Each chunk is short, but assign a large section_context to force truncation
        large_parent_chunks = [
            _make_result(
                text=f"Child {i}",
                score=round(1.0 - i * 0.1, 1),
                parent_id=f"p{i}",
                section_context="M" * 3000,
            )
            for i in range(10)
        ]
        # 10 chunks × ~3000 chars = ~30000, so must truncate under 12000
        output = _deduplicate_by_parent(large_parent_chunks, max_context_chars=12000)
        assert len(output) < 10
        total_chars = sum(len(d["text"]) for d in output)
        assert total_chars <= 12000

    def test_ordering_by_score_descending(self):
        """
        The final output should be sorted by score descending,
        so the highest-scoring entry comes first.
        """
        results = [
            _make_result(
                text="Low", score=0.1, parent_id="low_p", section_context="Low ctx."
            ),
            _make_result(
                text="High", score=0.9, parent_id="high_p", section_context="High ctx."
            ),
            _make_result(
                text="Mid", score=0.5, parent_id="mid_p", section_context="Mid ctx."
            ),
        ]
        output = _deduplicate_by_parent(results, max_context_chars=12000)
        assert len(output) == 3
        scores = [d["score"] for d in output]
        assert scores == sorted(scores, reverse=True)
