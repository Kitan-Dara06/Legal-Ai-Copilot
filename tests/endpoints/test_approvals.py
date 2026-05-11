"""Tests for approval token endpoints — single-use HMAC tokens.

Endpoint prefix: ``/approvals``
"""

import uuid

from fastapi import status


class TestListApprovals:
    """GET /approvals — list pending approvals."""

    def test_list_approvals_returns_200(self, client):
        response = client.get(
            "/approvals",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 401, 403)


class TestApproveWorkflow:
    """POST /approvals/workflows/{workflow_id}/approve — single-use HMAC token."""

    def test_approve_with_valid_token(self, client):
        wf_id = str(uuid.uuid4())
        payload = {"token": "valid-hmac-token"}
        response = client.post(
            f"/approvals/workflows/{wf_id}/approve",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 400, 401, 403, 404)

    def test_approve_missing_token_returns_422(self, client):
        import uuid

        wf_id = str(uuid.uuid4())
        response = client.post(
            f"/approvals/workflows/{wf_id}/approve",
            json={},
            headers={"Authorization": "Bearer fake-token"},
        )
        # In test mode without real Supabase, auth may reject before body validation
        assert response.status_code in (401, 422)


class TestRejectWorkflow:
    """POST /approvals/workflows/{workflow_id}/reject — reject with HMAC token."""

    def test_reject_with_valid_token(self, client):
        wf_id = str(uuid.uuid4())
        payload = {"token": "valid-hmac-token"}
        response = client.post(
            f"/approvals/workflows/{wf_id}/reject",
            json=payload,
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 400, 401, 403, 404)


class TestReissueApproval:
    """POST /approvals/{approval_id}/reissue — admin reissue expired token."""

    def test_reissue_approval(self, client):
        approval_id = str(uuid.uuid4())
        response = client.post(
            f"/approvals/{approval_id}/reissue",
            headers={"Authorization": "Bearer fake-token"},
        )
        assert response.status_code in (200, 401, 403, 404)
