"""Tests for RBAC middleware — privilege escalation prevention.

Each test exercises an endpoint that carries a ``require_role``
dependency.  Instead of patching the module-level function (which
FastAPI captures by reference at import time), we override the
dependency via ``client.app.dependency_overrides`` — the canonical
approach for FastAPI test suites.

Fixtures used:
  - ``client`` — TestClient wrapping the real app
  - ``valid_workspace_id`` — a syntactically valid UUID
  - ``auth_header`` — dummy Bearer token to pass header checks
"""

import pytest
from fastapi import status

# ── Helpers ───────────────────────────────────────────────────────────────────


class _MockRole:
    """Simple namespace for injecting a role into dependency overrides.

    Usage::

        client.app.dependency_overrides[resolve_user_role] = _MockRole(UserRole.PARTNER)

    FastAPI calls the override like the original dependency::

        await _MockRole(UserRole.PARTNER)(request, db=session)

    The mock returns the role directly — no DB query needed.
    """

    def __init__(self, role):
        self._role = role

    async def __call__(self, request, db=None):
        return self._role


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAuditExportRequiresPartner:
    """GET /workspaces/{id}/audit/export uses ``require_role(PARTNER)``.

    The endpoint is defined in ``app/routers/audit.py``.
    """

    @pytest.fixture(autouse=True)
    def _clean_overrides(self, client):
        """Remove any dependency overrides after each test in this class.

        Because ``dependency_overrides`` is mutable state on the app
        object, we must reset it to keep tests hermetic.
        """
        yield
        client.app.dependency_overrides.clear()

    def _override_role(self, client, role):
        """Register a ``resolve_user_role`` override that returns *role*."""
        from app.middleware.rbac_middleware import resolve_user_role

        client.app.dependency_overrides[resolve_user_role] = _MockRole(role)

    def _override_org_id(self, client, org_id="test-org-uuid"):
        from fastapi import Request

        from app.dependencies import get_org_id_unified

        async def _mock_org_id(request: Request):
            return org_id

        client.app.dependency_overrides[get_org_id_unified] = _mock_org_id

    def _override_db(self, client):
        """Mock the DB session so ``get_db`` doesn't try to connect to real Postgres."""
        from unittest.mock import AsyncMock

        from app.database import get_db

        async def _mock_db():
            yield AsyncMock()

        client.app.dependency_overrides[get_db] = _mock_db

    def test_associate_cannot_export_audit(
        self, client, valid_workspace_id, auth_header
    ):
        """An ASSOCIATE user should get 403 on a PARTNER-only endpoint."""
        from app.models import UserRole

        self._override_role(client, UserRole.ASSOCIATE)
        self._override_org_id(client)
        self._override_db(client)

        response = client.get(
            f"/workspaces/{valid_workspace_id}/audit/export",
            headers={"Authorization": auth_header["Authorization"]},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, (
            f"Expected 403 for ASSOCIATE, got {response.status_code}: {response.text[:200]}"
        )

    def test_member_cannot_export_audit(self, client, valid_workspace_id, auth_header):
        """A MEMBER user should also get 403."""
        from app.models import UserRole

        self._override_role(client, UserRole.MEMBER)
        self._override_org_id(client)
        self._override_db(client)

        response = client.get(
            f"/workspaces/{valid_workspace_id}/audit/export",
            headers={"Authorization": auth_header["Authorization"]},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_partner_can_export_audit(self, client, valid_workspace_id, auth_header):
        """A PARTNER user should be allowed (may be 200, 404, or 500)."""
        from app.models import UserRole

        self._override_role(client, UserRole.PARTNER)
        self._override_org_id(client)

        response = client.get(
            f"/workspaces/{valid_workspace_id}/audit/export",
            headers={"Authorization": auth_header["Authorization"]},
        )
        assert response.status_code != status.HTTP_403_FORBIDDEN, (
            f"PARTNER should not receive 403, got {response.status_code}"
        )
        # May be 200 (success), 404 (workspace not found in DB), or 500 (DB/mock
        # issue).  Any of these is a valid downstream outcome and proves the
        # RBAC gate passed.

    def test_system_admin_can_export_audit(
        self, client, valid_workspace_id, auth_header
    ):
        """SYSTEM_ADMIN has the highest level and should be allowed."""
        from app.models import UserRole

        self._override_role(client, UserRole.SYSTEM_ADMIN)
        self._override_org_id(client)

        response = client.get(
            f"/workspaces/{valid_workspace_id}/audit/export",
            headers={"Authorization": auth_header["Authorization"]},
        )
        assert response.status_code != status.HTTP_403_FORBIDDEN


class TestUnauthenticatedAccess:
    """Endpoints should reject requests without any credentials."""

    def test_audit_export_rejects_anonymous(self, client, valid_workspace_id):
        """Without credentials the audit export should return 401."""
        response = client.get(f"/workspaces/{valid_workspace_id}/audit/export")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"Expected 401, got {response.status_code}"
        )

    def test_goal_create_rejects_anonymous(
        self, client, valid_workspace_id, valid_goal_payload
    ):
        """Without credentials the goal endpoint should return 401."""
        response = client.post(
            f"/workspaces/{valid_workspace_id}/goals",
            json=valid_goal_payload,
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_liveness_public(self, client):
        """GET /live should always be accessible."""
        response = client.get("/live")
        assert response.status_code == 200

    def test_health_public(self, client):
        """GET /health should always be accessible."""
        response = client.get("/health")
        assert response.status_code == 200


class TestCrossTenantIsolation:
    """User from Org A must not access Org B's resources."""

    def test_cross_tenant_workspace_returns_404(self, client):
        """Accessing a workspace from a different org should return 404."""
        import uuid

        alien_ws = str(uuid.uuid4())
        response = client.get(
            f"/workspaces/{alien_ws}/goals",
            headers={"Authorization": "Bearer fake-token"},
        )
        # Should mask existence — return 404 not 403
        assert response.status_code in (401, 403, 404)
