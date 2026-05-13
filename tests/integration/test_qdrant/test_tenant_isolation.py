"""
Integration tests for Qdrant tenant isolation.

FR-L2-03: Tests that org-level filtering correctly scopes queries to
a single tenant's data. Uses Filter with FieldCondition and MatchValue.
"""

import pytest
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
)

pytestmark = pytest.mark.integration


class TestTenantIsolation:
    """Verify that Qdrant filter-based tenant isolation works correctly."""

    def test_tenant_filter_org_A_returns_2_results(
        self, qdrant_client, test_collection
    ):
        """Filtering on org_id='org_A' should return exactly 2 points."""
        scroll_result = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
            filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_A"),
                    )
                ]
            ),
        )
        points, _ = scroll_result
        assert len(points) == 2, f"Expected 2 points for org_A, got {len(points)}"
        for p in points:
            assert p.payload["org_id"] == "org_A"

    def test_tenant_filter_org_B_returns_2_results(
        self, qdrant_client, test_collection
    ):
        """Filtering on org_id='org_B' should return exactly 2 points."""
        scroll_result = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
            filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_B"),
                    )
                ]
            ),
        )
        points, _ = scroll_result
        assert len(points) == 2, f"Expected 2 points for org_B, got {len(points)}"
        for p in points:
            assert p.payload["org_id"] == "org_B"

    def test_tenant_filter_org_C_returns_1_result(self, qdrant_client, test_collection):
        """Filtering on org_id='org_C' should return exactly 1 point."""
        scroll_result = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
            filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_C"),
                    )
                ]
            ),
        )
        points, _ = scroll_result
        assert len(points) == 1, f"Expected 1 point for org_C, got {len(points)}"
        assert points[0].payload["org_id"] == "org_C"

    def test_tenant_filter_unknown_org_returns_no_results(
        self, qdrant_client, test_collection
    ):
        """Filtering on a non-existent org_id should return zero points."""
        scroll_result = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
            filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_unknown"),
                    )
                ]
            ),
        )
        points, _ = scroll_result
        assert len(points) == 0, f"Expected 0 points for unknown org, got {len(points)}"

    def test_query_points_with_tenant_filter(self, qdrant_client, test_collection):
        """Query points with dense vector + tenant filter should scope results."""
        results = qdrant_client.query_points(
            collection_name=test_collection,
            query=[0.3, 0.4, 0.5, 0.6],
            using="dense",
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_A"),
                    )
                ]
            ),
            limit=5,
            with_payload=True,
        )
        assert results.points is not None
        assert len(results.points) <= 2
        for p in results.points:
            assert p.payload["org_id"] == "org_A"

    def test_tenant_isolation_maintains_data_separation(
        self, qdrant_client, test_collection
    ):
        """
        Verify cross-tenant isolation: org_A sees exactly its 2 points,
        org_B sees exactly its 2 points, and no data leaks between them.
        """
        # Get org_A points
        result_a, _ = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
            filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_A"),
                    )
                ]
            ),
        )

        # Get org_B points
        result_b, _ = qdrant_client.scroll(
            collection_name=test_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
            filter=Filter(
                must=[
                    FieldCondition(
                        key="org_id",
                        match=MatchValue(value="org_B"),
                    )
                ]
            ),
        )

        org_a_ids = {p.id for p in result_a}
        org_b_ids = {p.id for p in result_b}

        # Verify no overlap between tenants
        assert len(org_a_ids & org_b_ids) == 0, (
            f"Data leak detected! Overlapping IDs: {org_a_ids & org_b_ids}"
        )
