import pytest

from .conftest import neo4j_unavailable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        neo4j_unavailable,
        reason="Neo4j is not available — NEO4J_URI not set",
    ),
]

WORKSPACE_ID = "test_ws"


def _make_chunk(hierarchy, text, deps=None):
    return {
        "hierarchy": hierarchy,
        "text": text,
        "dependencies_clauses": deps or [],
    }


class TestBuildGraph:
    """Tests for DependencyGraph.build_graph()."""

    def test_creates_correct_number_of_clause_nodes(self, clean_graph):
        """2 chunks should produce exactly 2 Clause nodes."""
        chunks = [
            _make_chunk(["Section 1"], "First clause."),
            _make_chunk(["Section 2"], "Second clause."),
        ]
        clean_graph.build_graph(chunks, doc_name="doc_1", workspace_id=WORKSPACE_ID)

        assert clean_graph.number_of_nodes() == 2

    def test_idempotent_no_duplicates(self, clean_graph):
        """Calling build_graph twice with the same data should not create duplicates."""
        chunks = [
            _make_chunk(["Section 1"], "First clause."),
            _make_chunk(["Section 2"], "Second clause."),
        ]
        clean_graph.build_graph(chunks, doc_name="doc_1", workspace_id=WORKSPACE_ID)
        clean_graph.build_graph(chunks, doc_name="doc_1", workspace_id=WORKSPACE_ID)

        assert clean_graph.number_of_nodes() == 2
        assert clean_graph.number_of_edges() == 0

    def test_dependencies_clauses_create_references_edges(self, clean_graph):
        """Chunks with dependencies_clauses should produce REFERENCES edges."""
        chunks = [
            _make_chunk(["Section 1"], "Root clause."),
            _make_chunk(
                ["Section 2"],
                "Depends on Section 1.",
                deps=["Section 1"],
            ),
            _make_chunk(
                ["Section 3"],
                "Depends on Section 2.",
                deps=["Section 2"],
            ),
        ]
        clean_graph.build_graph(chunks, doc_name="doc_1", workspace_id=WORKSPACE_ID)

        assert clean_graph.number_of_nodes() == 3
        assert clean_graph.number_of_edges() == 2
