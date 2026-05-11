import pytest

from .conftest import neo4j_unavailable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        neo4j_unavailable,
        reason="Neo4j is not available — NEO4J_URI not set",
    ),
]

WS_1 = "ws_1"
WS_2 = "ws_2"


def _make_chunk(hierarchy, text, deps=None):
    return {
        "hierarchy": hierarchy,
        "text": text,
        "dependencies_clauses": deps or [],
    }


class TestTenantIsolation:
    """Clause nodes in one workspace must not be visible from another."""

    WS1_CHUNKS = [
        _make_chunk(["Section 1"], "Clause in workspace 1."),
        _make_chunk(["Section 2"], "Another clause in workspace 1."),
    ]

    WS2_CHUNKS = [
        _make_chunk(["Section A"], "Clause in workspace 2."),
        _make_chunk(["Section B"], "Another clause in workspace 2."),
    ]

    @pytest.fixture(autouse=True)
    def _build_two_workspaces(self, clean_graph):
        self.graph = clean_graph
        # Populate workspace 1
        self.graph.build_graph(
            self.WS1_CHUNKS,
            doc_name="doc_ws1",
            workspace_id=WS_1,
        )
        # Populate workspace 2
        self.graph.build_graph(
            self.WS2_CHUNKS,
            doc_name="doc_ws2",
            workspace_id=WS_2,
        )

    def test_can_query_own_workspace(self):
        """Querying from ws_1 should return ws_1 nodes."""
        chain = self.graph.get_dependency_chains(
            "Section 1",
            max_hops=1,
            workspace_id=WS_1,
        )
        node_ids = {r["node"] for r in chain}
        assert "Section 1" in node_ids, "Should find Section 1 when querying from ws_1"

    def test_cross_workspace_query_returns_empty(self):
        """Querying a ws_1 node from ws_2 coordinates should return nothing."""
        chain = self.graph.get_dependency_chains(
            "Section 1",
            max_hops=5,
            workspace_id=WS_2,
        )
        # The start node doesn't exist in ws_2, so OPTIONAL MATCH yields no rows
        assert len(chain) == 0, "Should not find ws_1 nodes when querying from ws_2"

    def test_workspace_two_is_isolated_from_one(self):
        """Querying from ws_2 should only see ws_2 nodes."""
        chain = self.graph.get_dependency_chains(
            "Section A",
            max_hops=1,
            workspace_id=WS_2,
        )
        node_ids = {r["node"] for r in chain}
        assert "Section A" in node_ids
        assert "Section 1" not in node_ids, "ws_1 nodes must not leak into ws_2 queries"
