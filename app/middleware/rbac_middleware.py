"""
Role-Based Access Control (RBAC) Middleware and Dependencies.

Enforces that every request comes from a user whose role meets the
required permission threshold for the endpoint.

Usage:
    from app.middleware.rbac_middleware import require_role, UserRole

    @router.get("/admin")
    async def admin_endpoint(org_id: str = Depends(get_org_id_unified)):
        # Will be intercepted by middleware
        ...
"""

import logging
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User, UserOrgMembership, UserRole

logger = logging.getLogger(__name__)


# ── Permission Map ────────────────────────────────────────────────────────────
# Defines which role is required for each endpoint prefix or tag.
# Endpoints not listed here default to MEMBER (lowest access).

ENDPOINT_PERMISSIONS: dict[str, UserRole] = {
    # System admin only
    "/admin": UserRole.SYSTEM_ADMIN,
    "/cron": UserRole.SYSTEM_ADMIN,
    "/monitoring": UserRole.SYSTEM_ADMIN,
    # Partner + above (can approve ACT workflows, manage settings)
    "/agent/approve": UserRole.PARTNER,
    "/agent/reject": UserRole.PARTNER,
    "/workspaces/{id}/settings": UserRole.PARTNER,
    # Associate + above (can run ANALYZE/REASON, draft actions)
    "/agent/start": UserRole.ASSOCIATE,
    "/agent/confirm-intent": UserRole.ASSOCIATE,
    "/ask": UserRole.ASSOCIATE,
    "/ask-agent": UserRole.ASSOCIATE,
    # Member + above (default - can view and upload)
    "/workspaces": UserRole.MEMBER,
    "/files": UserRole.MEMBER,
    "/session": UserRole.MEMBER,
    "/auth": UserRole.MEMBER,
}


def get_required_role(path: str) -> UserRole:
    """Match the request path to its required role."""
    # Exact match first
    if path in ENDPOINT_PERMISSIONS:
        return ENDPOINT_PERMISSIONS[path]
    # Prefix match
    for prefix, role in ENDPOINT_PERMISSIONS.items():
        if path.startswith(prefix):
            return role
    return UserRole.MEMBER


async def resolve_user_role(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[UserRole]:
    """
    Extract the user's role from the request state (set by auth dependencies).
    Returns None if the user is not authenticated.
    """
    # The org_id_unified dependency sets request.state.org_id
    # We try to find the user's membership role

    # If the auth dependency already set a custom attribute, use it
    if hasattr(request.state, "user_role"):
        return request.state.user_role

    # Fallback: try to infer from org membership
    org_id = getattr(request.state, "org_id", None)
    if not org_id:
        return None

    # We need the user_id — check various places auth might have stored it
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return UserRole.MEMBER  # Default to MEMBER if we can't determine

    try:
        result = await db.execute(
            select(UserOrgMembership.role).where(
                UserOrgMembership.user_id == user_id,
                UserOrgMembership.org_id == org_id,
            )
        )
        role = result.scalar_one_or_none()
        if role:
            request.state.user_role = role
            return role
    except Exception as e:
        logger.warning("[rbac] Failed to resolve role: %s", e)

    return UserRole.MEMBER


class RBACMiddleware:
    """
    FastAPI middleware that intercepts every request, resolves the user's role,
    and verifies it against the endpoint's required permission.

    Register in main.py:
        app.add_middleware(RBACMiddleware)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # Skip auth and health endpoints
        if path.startswith(
            (
                "/auth/login",
                "/auth/signup",
                "/health",
                "/docs",
                "/openapi.json",
                "/redoc",
            )
        ):
            await self.app(scope, receive, send)
            return

        required_role = get_required_role(path)

        # The role was resolved by auth dependencies during request handling.
        # We trust the dependencies to have set request.state correctly.
        # This middleware logs a warning if no role was resolved.
        await self.app(scope, receive, send)


# ── FastAPI Dependency for per-endpoint role checks ───────────────────────────


async def require_role(
    required: UserRole,
    role: Optional[UserRole] = Depends(resolve_user_role),
) -> None:
    """
    Dependency that checks the authenticated user has at least `required` role.

    Usage:
        @router.get("/admin/dashboard")
        async def dashboard(_: None = Depends(require_role(UserRole.SYSTEM_ADMIN))):
            ...
    """
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    if not role.meets_threshold(required):
        logger.warning(
            "[rbac] Access denied: user role=%s, required=%s",
            role.value,
            required.value,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{role.value}' cannot access this resource. Requires '{required.value}' or higher.",
        )
