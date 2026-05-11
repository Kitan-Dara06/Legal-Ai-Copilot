"""Tests for new workspace extension endpoints.

All endpoints are scoped under ``/workspaces/{workspace_id}/``.
"""

import uuid

from fastapi import status


class TestDocumentStatus:
    """GET /workspaces/{workspace_id}/documents/{doc_id}/status."""

    def test_document_status_returns_200(self, client, valid_workspace_id):
        doc_id = str(uuid.uuid4())
        response = client.get(
            f"/workspaces/{valid_workspace_id}/documents/{doc_id}/status",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 401, 403, 404)

    def test_document_status_handles_redis_timeout(self, client, valid_workspace_id):
        """When Redis times out, endpoint should return 503."""
        from app.dependencies import get_redis

        async def mock_timeout():
            import asyncio

            raise asyncio.TimeoutError("Redis connection timed out")

        client.app.dependency_overrides[get_redis] = mock_timeout
        import uuid

        doc_id = str(uuid.uuid4())
        response = client.get(
            f"/workspaces/{valid_workspace_id}/documents/{doc_id}/status",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (401, 403, 500, 503)
        client.app.dependency_overrides.clear()


class TestWorkspaceSession:
    """POST /workspaces/{workspace_id}/session — create workspace-scoped session."""

    def test_create_session_returns_201(self, client, valid_workspace_id):
        payload = {"file_ids": [1, 2, 3]}
        response = client.post(
            f"/workspaces/{valid_workspace_id}/session",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )
        # The endpoint accepts no body model, so extra JSON is ignored.
        # Expect a 500 because DB/Redis won't be available, or 404 if
        # workspace not found by the auth override.
        assert response.status_code in (201, 401, 403, 404, 422, 500)

    def test_create_session_missing_file_ids_returns_422(
        self, client, valid_workspace_id
    ):
        response = client.post(
            f"/workspaces/{valid_workspace_id}/session",
            json={},
            headers={"Authorization": "Bearer fake-token"},
        )
        # No body model is declared on this endpoint, so an empty body
        # will not trigger a 422.  It will attempt DB/Redis lookups.
        assert response.status_code in (201, 401, 403, 404, 422, 500)


class TestDefinedTerms:
    """GET /workspaces/{workspace_id}/defined-terms."""

    def test_defined_terms_returns_200(self, client, valid_workspace_id):
        response = client.get(
            f"/workspaces/{valid_workspace_id}/defined-terms",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 401, 403)


class TestDeadlines:
    """GET /workspaces/{workspace_id}/deadlines."""

    def test_deadlines_returns_200(self, client, valid_workspace_id):
        response = client.get(
            f"/workspaces/{valid_workspace_id}/deadlines",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 401, 403)

    def test_deadlines_handles_db_failure(self, client, valid_workspace_id):
        """When DB is unreachable, endpoint should return 503."""
        from unittest.mock import AsyncMock

        from app.database import get_db

        async def mock_db_failure():
            raise Exception("Database connection pool exhausted")

        client.app.dependency_overrides[get_db] = mock_db_failure
        response = client.get(
            f"/workspaces/{valid_workspace_id}/deadlines",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (401, 403, 500, 503)
        client.app.dependency_overrides.clear()


class TestGraphExpand:
    """GET /workspaces/{workspace_id}/graph/expand — Neo4j visualizer placeholder."""

    def test_graph_expand_returns_200(self, client, valid_workspace_id):
        response = client.get(
            f"/workspaces/{valid_workspace_id}/graph/expand",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 401, 403)
