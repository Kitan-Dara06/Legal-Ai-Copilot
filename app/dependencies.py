import asyncio
import hashlib
import os
import time
from dataclasses import dataclass

import httpx
import sentry_sdk
from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiKey, Invite, Organization, User, UserOrgMembership, UserRole

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_JWKS_URL = (
    f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
    if SUPABASE_URL
    else None
)
SUPABASE_JWT_AUD = os.getenv("SUPABASE_JWT_AUD", "authenticated")

_supabase_jwks_cache: dict | None = None
_supabase_jwks_fetched_at: float = 0.0
_JWKS_TTL_SECONDS = 86400  # Re-fetch JWKS once per day


@dataclass
class AuthContext:
    org_id: str
    user: User
    api_key: ApiKey


@dataclass
class SupabaseAuthContext:
    org_id: str
    user: User
    claims: dict


def hash_api_key(raw_key: str) -> str:
    """
    Hash the key so we never store or compare raw secrets directly.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def get_auth_context(
    request: Request,
    api_key_value: str = Security(api_key_header),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """
    Validates the API key hash, prevents timing attacks, and sets up rate-limiting state.
    """
    hashed_key = hash_api_key(api_key_value)

    stmt = (
        select(ApiKey, User)
        .join(User, ApiKey.user_id == User.id)
        .where(ApiKey.key_hash == hashed_key, ApiKey.is_active.is_(True))
    )
    result = await db.execute(stmt)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, missing, or revoked API Key",
        )

    api_key, user = row

    # Attach org_id to the request state so SlowAPI can rate limit per-org
    request.state.org_id = api_key.org_id

    # Set Sentry user context
    sentry_sdk.set_user(
        {"id": str(user.id), "email": user.email, "org_id": str(api_key.org_id)}
    )

    return AuthContext(org_id=api_key.org_id, user=user, api_key=api_key)


async def get_admin_auth_context(
    ctx: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """
    Dependency to ensure the authenticated user is an admin for their ACTIVE organization.
    Checks UserOrgMembership.role rather than the deprecated User.role column.
    """
    stmt = select(UserOrgMembership.role).where(
        UserOrgMembership.user_id == ctx.user.id,
        UserOrgMembership.org_id == ctx.org_id,
    )
    result = await db.execute(stmt)
    membership_role = result.scalar_one_or_none()

    if membership_role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required to perform this action",
        )
    return ctx


async def get_org_id(ctx: AuthContext = Depends(get_auth_context)) -> str:
    """
    Convenience dependency to extract only the org_id.
    Useful for existing endpoints that just need to namespace vector searches / DB queries.
    """
    return ctx.org_id


def get_org_id_for_rate_limit(request: Request) -> str:
    """
    Custom key function for SlowAPI to rate limit by Org instead of IP.
    """
    return getattr(
        request.state, "org_id", request.client.host if request.client else "127.0.0.1"
    )


async def get_supabase_claims(
    request: Request,
) -> dict:
    """
    Verifies a Supabase JWT from the Authorization: Bearer <token> header.
    Returns the decoded claims dict.

    Uses Supabase's JWKS (ES256) to verify signature and audience.
    """
    if not SUPABASE_JWKS_URL:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase JWKS URL not configured on server.",
        )

    auth_header = request.headers.get("Authorization")
    import logging

    logger = logging.getLogger("app.dependencies")

    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning(
            "[get_supabase_claims] Missing or invalid Authorization header: %s. All headers: %s",
            auth_header,
            dict(request.headers),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )

    token = auth_header.split(" ", 1)[1].strip()
    return await _verify_supabase_token(token)


async def _refresh_supabase_jwks_if_needed() -> None:
    """
    Refreshes Supabase JWKS cache if empty or stale.
    Uses run_in_executor so the blocking network call does not block the event loop.
    """
    global _supabase_jwks_cache, _supabase_jwks_fetched_at
    now = time.monotonic()
    if (
        _supabase_jwks_cache is not None
        and (now - _supabase_jwks_fetched_at) <= _JWKS_TTL_SECONDS
    ):
        return

    headers = {}
    if SUPABASE_ANON_KEY:
        headers["apikey"] = SUPABASE_ANON_KEY

    url = SUPABASE_JWKS_URL
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase JWKS URL not configured on server.",
        )

    def _fetch_sync() -> dict:
        resp = httpx.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    from typing import Any, Callable

    _fetch: Callable[[], Any] = _fetch_sync

    try:
        loop = asyncio.get_running_loop()
        jwks = await loop.run_in_executor(None, _fetch)
        _supabase_jwks_cache = jwks
        _supabase_jwks_fetched_at = time.monotonic()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to fetch Supabase JWKS.",
        )


async def _verify_supabase_token(token: str) -> dict:
    """
    Verifies a Supabase JWT string; returns decoded claims or raises HTTPException.
    """
    if not SUPABASE_JWKS_URL:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Supabase JWKS URL not configured on server.",
        )

    # Decode header to get kid and alg
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase token.",
        )

    kid = header.get("kid")
    alg = header.get("alg")
    if not kid or not alg:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase token header.",
        )

    await _refresh_supabase_jwks_if_needed()

    keys = _supabase_jwks_cache.get("keys", []) if _supabase_jwks_cache else []
    key_data = next((k for k in keys if k.get("kid") == kid), None)
    if not key_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase token key id.",
        )

    public_key = jwk.construct(key_data)

    # Verify signature manually, then decode claims with audience check
    message, encoded_sig = token.rsplit(".", 1)
    try:
        decoded_sig = base64url_decode(encoded_sig.encode("utf-8"))
        if not public_key.verify(message.encode("utf-8"), decoded_sig):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Supabase token signature.",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Supabase token signature.",
        )

    try:
        claims = jwt.decode(
            token,
            public_key.to_pem().decode("utf-8"),
            algorithms=[alg],
            audience=SUPABASE_JWT_AUD,
            options={"verify_exp": True, "verify_aud": True},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Supabase token is invalid or expired.",
        ) from exc

    return claims


async def _build_supabase_auth_context(
    claims: dict, db: AsyncSession
) -> SupabaseAuthContext:
    """
    Maps verified Supabase JWT claims to a local User + Organization.
    - If User exists for this Supabase sub → return their org.
    - Else if a pending Invite exists for this email → join that org as MEMBER and mark invite accepted.
    - Else → create a new org and user as ADMIN.
    """
    from datetime import datetime, timezone

    sub = claims.get("sub")
    email = claims.get("email")

    if not sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supabase token missing required claims.",
        )

    stmt = select(User).where(User.supabase_user_id == sub)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user:
        resolved_org = user.personal_org_id or user.org_id
        # Set Sentry user context
        sentry_sdk.set_user(
            {"id": str(user.id), "email": email, "org_id": str(resolved_org)}
        )
        return SupabaseAuthContext(org_id=str(resolved_org), user=user, claims=claims)

    # No user by sub. Maybe they exist by email (e.g. legacy signup or another Supabase link).
    # Link this Supabase account to the existing user to avoid duplicate key on email.
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        user.supabase_user_id = sub
        await db.commit()
        await db.refresh(user)
        resolved_org = user.personal_org_id or user.org_id
        return SupabaseAuthContext(org_id=str(resolved_org), user=user, claims=claims)

    # First time we see this Supabase user. Check for a pending invite (magic-link flow).
    now = datetime.now(timezone.utc)
    stmt = (
        select(Invite)
        .where(
            Invite.email == email,
            Invite.is_accepted.is_(False),
            Invite.expires_at > now,
        )
        .order_by(Invite.created_at.desc())
        .limit(1)
    )
    invite_result = await db.execute(stmt)
    invite = invite_result.scalar_one_or_none()

    if invite:
        # Auto-accept: create user + membership for the inviting org
        import uuid as _uuid

        from passlib.hash import bcrypt as _bcrypt

        new_user = User(
            id=_uuid.uuid4(),
            email=email,
            supabase_user_id=sub,
            hashed_password=_bcrypt.hash(
                _uuid.uuid4().hex
            ),  # placeholder — login is via Supabase
            org_id=invite.org_id,
            personal_org_id=invite.org_id,
            role=UserRole.MEMBER,
        )
        db.add(new_user)

        membership = UserOrgMembership(
            user_id=new_user.id,
            org_id=invite.org_id,
            role=UserRole.MEMBER,
        )
        db.add(membership)

        invite.is_accepted = True
        await db.commit()
        await db.refresh(new_user)

        # Set Sentry user context
        sentry_sdk.set_user(
            {"id": str(new_user.id), "email": email, "org_id": str(invite.org_id)}
        )

        return SupabaseAuthContext(
            org_id=str(invite.org_id), user=new_user, claims=claims
        )

    # No invite and no existing account: the user must register explicitly
    # so they can choose their own org_id. We return a structured 403 that
    # the frontend detects and redirects to the org-creation / signup form.
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "setup_required",
            "message": (
                "No account found for this email. "
                "Please sign up and choose your organisation ID, "
                "or ask your organisation admin to invite you."
            ),
        },
    )


from fastapi import Header
async def get_supabase_auth_context(
    request: Request,
    claims: dict = Depends(get_supabase_claims),
    db: AsyncSession = Depends(get_db),
    x_active_org: str | None = Header(None, alias="X-Active-Org"),
) -> SupabaseAuthContext:
    """
    Maps a verified Supabase JWT to a local User + Organization.
    Respects X-Active-Org header for switching active workspaces.
    """
    ctx = await _build_supabase_auth_context(claims, db)
    
    if x_active_org:
        # Check if org exists
        org_res = await db.execute(select(Organization).where(Organization.slug == x_active_org))
        org = org_res.scalar_one_or_none()
        
        if org:
            # Check user membership
            mem_res = await db.execute(
                select(UserOrgMembership)
                .where(UserOrgMembership.user_id == ctx.user.id, UserOrgMembership.org_id == org.id)
            )
            if mem_res.scalar_one_or_none():
                # valid membership in the requested org
                ctx.org_id = str(org.id)
                request.state.org_id = org.id
                return ctx

        # If not found or not a member, fallback to the default (or could raise 403)
        # We stick to the default so the app doesn't hard crash
        
    return ctx


async def _get_auth_context_from_key(
    api_key_value: str, request: Request, db: AsyncSession
) -> AuthContext:
    """Validates API key and returns AuthContext; raises HTTPException on failure."""
    hashed_key = hash_api_key(api_key_value)
    stmt = (
        select(ApiKey, User)
        .join(User, ApiKey.user_id == User.id)
        .where(ApiKey.key_hash == hashed_key, ApiKey.is_active.is_(True))
    )
    result = await db.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, missing, or revoked API Key",
        )
    api_key, user = row
    request.state.org_id = api_key.org_id
    return AuthContext(org_id=api_key.org_id, user=user, api_key=api_key)


async def get_org_id_unified(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    x_active_org: str | None = Header(None, alias="X-Active-Org"),
) -> str:
    """
    Strict org resolution:
    - Bearer (Supabase): require membership in X-Active-Org if provided; else choose single membership or personal_org_id; else 400.
    - API key: allow optional X-Active-Org slug to assert namespace; must match key org.
    """

    # Helper: resolve org_id from slug and membership
    async def _resolve_for_user(user_id, personal_org_id):
        if x_active_org:
            org_res = await db.execute(
                select(Organization).where(Organization.slug == x_active_org)
            )
            org = org_res.scalar_one_or_none()
            if not org:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Organization not found.",
                )
            mem = await db.execute(
                select(UserOrgMembership).where(
                    UserOrgMembership.user_id == user_id,
                    UserOrgMembership.org_id == org.id,
                )
            )
            if mem.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of that organization.",
                )
            return str(org.id)
        # No header: check memberships
        mem_rows = (
            (
                await db.execute(
                    select(UserOrgMembership.org_id).where(
                        UserOrgMembership.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not mem_rows:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No organization memberships found.",
            )
        if len(mem_rows) == 1:
            return str(mem_rows[0])
        if personal_org_id:
            return str(personal_org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Active-Org header is required for users in multiple organizations.",
        )

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        claims = await _verify_supabase_token(token)
        ctx = await _build_supabase_auth_context(claims, db)
        org_id = await _resolve_for_user(ctx.user.id, ctx.user.personal_org_id)
        request.state.org_id = org_id
        return org_id

    if x_api_key:
        ctx = await _get_auth_context_from_key(x_api_key, request, db)
        # API key is already scoped to an org; ensure slug matches when provided
        if x_active_org:
            org_res = await db.execute(
                select(Organization).where(Organization.slug == x_active_org)
            )
            org = org_res.scalar_one_or_none()
            if not org:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Organization not found.",
                )
            if str(org.id) != str(ctx.org_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API key is not scoped to the requested organization.",
                )
            request.state.org_id = str(org.id)
            return str(org.id)
        request.state.org_id = str(ctx.org_id)
        return str(ctx.org_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing credentials. Provide Authorization: Bearer <token> or X-API-Key.",
    )


async def get_supabase_org_id(
    ctx: SupabaseAuthContext = Depends(get_supabase_auth_context),
) -> str:
    return ctx.org_id


async def get_supabase_admin_context(
    ctx: SupabaseAuthContext = Depends(get_supabase_auth_context),
    db: AsyncSession = Depends(get_db),
) -> SupabaseAuthContext:
    """Requires the authenticated Supabase user to be an ADMIN for the active org."""
    from uuid import UUID as _UUID

    org_uuid = _UUID(ctx.org_id) if isinstance(ctx.org_id, str) else ctx.org_id
    stmt = select(UserOrgMembership.role).where(
        UserOrgMembership.user_id == ctx.user.id,
        UserOrgMembership.org_id == org_uuid,
    )
    result = await db.execute(stmt)
    membership_role = result.scalar_one_or_none()

    if membership_role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return ctx
