"""
Integration tests for Qdrant hybrid search (dense + sparse fusion).

FR-L3-01 / Priority 1: Tests basic hybrid search by calling qdrant_client
directly with a Prefetch for dense + sparse fusion, then asserting results
are returned and sorted by score.
"""

import pytest
from qdrant_client.models import (
    Fusion,
    FusionQuery,
    Prefetch,
    SparseVector,
)

pytestmark = pytest.mark.integration


class TestHybridSearch:
    """Verify that hybrid (dense + sparse) fusion search works correctly."""

    def test_dense_only_search_returns_results(self, qdrant_client, test_collection):
        """A basic dense-vector search should return 5 points (no filter)."""
        results = qdrant_client.query_points(
            collection_name=test_collection,
            query=[0.3, 0.4, 0.5, 0.6],
            using="dense",
            limit=5,
            with_payload=True,
        )
        assert results.points is not None
        assert len(results.points) == 5

    def test_sparse_only_search_returns_results(self, qdrant_client, test_collection):
        """A sparse-vector search should return points."""
        sparse_query = SparseVector(indices=[0, 1], values=[0.5, 0.5])
        results = qdrant_client.query_points(
            collection_name=test_collection,
            query=sparse_query,
            using="sparse",
            limit=5,
            with_payload=True,
        )
        assert results.points is not None
        assert len(results.points) > 0

    def test_hybrid_search_with_prefetch_and_fusion(
        self, qdrant_client, test_collection
    ):
        """Hybrid search with RRF fusion returns results sorted by score."""
        dense_query = [0.3, 0.4, 0.5, 0.6]
        sparse_query = SparseVector(indices=[0, 1], values=[0.5, 0.5])

        prefetch = [
            Prefetch(query=dense_query, using="dense", limit=5),
            Prefetch(query=sparse_query, using="sparse", limit=5),
        ]

        results = qdrant_client.query_points(
            collection_name=test_collection,
            prefetch=prefetch,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=5,
            with_payload=True,
        )

        assert results.points is not None
        assert len(results.points) > 0

        # Verify results are sorted by score descending
        scores = [p.score for p in results.points]
        assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)), (
            f"Results not sorted by score: {scores}"
        )

    def test_hybrid_search_with_limit(self, qdrant_client, test_collection):
        """Hybrid search with a limit of 2 should return exactly 2 results."""
        dense_query = [0.3, 0.4, 0.5, 0.6]
        sparse_query = SparseVector(indices=[0, 1], values=[0.5, 0.5])

        prefetch = [
            Prefetch(query=dense_query, using="dense", limit=5),
            Prefetch(query=sparse_query, using="sparse", limit=5),
        ]

        results = qdrant_client.query_points(
            collection_name=test_collection,
            prefetch=prefetch,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=2,
            with_payload=True,
        )

        assert results.points is not None
        assert len(results.points) == 2

    def test_hybrid_search_payload_contains_expected_fields(
        self, qdrant_client, test_collection
    ):
        """Each result should contain title, org_id, text, and page in payload."""
        dense_query = [0.3, 0.4, 0.5, 0.6]
        sparse_query = SparseVector(indices=[0, 1], values=[0.5, 0.5])

        prefetch = [
            Prefetch(query=dense_query, using="dense", limit=5),
            Prefetch(query=sparse_query, using="sparse", limit=5),
        ]

        results = qdrant_client.query_points(
            collection_name=test_collection,
            prefetch=prefetch,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=5,
            with_payload=True,
        )

        for point in results.points:
            payload = point.payload or {}
            assert "title" in payload, f"Point {point.id} missing 'title'"
            assert "org_id" in payload, f"Point {point.id} missing 'org_id'"
            assert "text" in payload, f"Point {point.id} missing 'text'"
            assert "page" in payload, f"Point {point.id} missing 'page'"

    def test_hybrid_search_with_scroll_returns_same_points(
        self, qdrant_client, test_collection
    ):
        """Scroll API should retrieve all 5 points with full payload."""
        scroll_result = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        points, next_page_offset = scroll_result
        assert len(points) == 5
        assert next_page_offset is None  # No more pages
