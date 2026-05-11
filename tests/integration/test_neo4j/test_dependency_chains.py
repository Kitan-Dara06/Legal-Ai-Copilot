import pytest

from .conftest import neo4j_unavailable

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        neo4j_unavailable,
        reason="Neo4j is not available — NEO4J_URI not set",
    ),
]

WORKSPACE_ID = "test_chains"


def _make_chunk(hierarchy, text, deps=None):
    return {
        "hierarchy": hierarchy,
        "text": text,
        "dependencies_clauses": deps or [],
    }


class TestDependencyChains:
    """
    Builds a chain A → B → C → D where each clause references the next.

    Edge direction is (src)-[:REFERENCES]->(tgt) where src depends on tgt,
    so the dependency traversal follows outgoing REFERENCES edges.
    """

    CHAIN_CHUNKS = [
        # A depends on B
        _make_chunk(["A"], "Top-level clause.", deps=["B"]),
        # B depends on C
        _make_chunk(["B"], "Intermediate clause.", deps=["C"]),
        # C depends on D
        _make_chunk(["C"], "Another intermediate clause.", deps=["D"]),
        # D — root of the chain, no further dependencies
        _make_chunk(["D"], "Leaf clause."),
    ]

    @pytest.fixture(autouse=True)
    def _build_chain(self, clean_graph):
        self.graph = clean_graph
        self.graph.build_graph(
            self.CHAIN_CHUNKS,
            doc_name="chain_doc",
            workspace_id=WORKSPACE_ID,
        )

    def _node_names(self, *, max_hops):
        results = self.graph.get_dependency_chains(
            "A",
            max_hops=max_hops,
            workspace_id=WORKSPACE_ID,
        )
        return {r["node"] for r in results}

    def test_max_hops_2_excludes_deep_nodes(self):
        """With max_hops=2, D should NOT appear in the chain results."""
        nodes = self._node_names(max_hops=2)
        assert "D" not in nodes, (
            f"D should not be reachable in 2 hops, but got nodes {nodes}"
        )

    def test_max_hops_5_reaches_leaf(self):
        """With max_hops=5, D should be reachable."""
        nodes = self._node_names(max_hops=5)
        assert "D" in nodes, (
            f"D should be reachable within 5 hops, but got nodes {nodes}"
        )

    def test_chain_contains_b_and_c(self):
        """B and C should appear regardless of max_hops >= 2."""
        nodes_2 = self._node_names(max_hops=2)
        nodes_5 = self._node_names(max_hops=5)

        assert "B" in nodes_2
        assert "C" in nodes_2
        assert "B" in nodes_5
        assert "C" in nodes_5
