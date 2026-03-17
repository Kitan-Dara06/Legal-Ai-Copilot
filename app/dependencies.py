import hashlib
import os
import time
from dataclasses import dataclass

import httpx
from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ApiKey, Invite, Organization, User, UserRole

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPERBASE_KEY")
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
        .where(ApiKey.key_hash == hashed_key, ApiKey.is_active == True)
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

    return AuthContext(org_id=api_key.org_id, user=user, api_key=api_key)


async def get_admin_auth_context(
    ctx: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """
    Dependency to ensure the authenticated user is an admin for their organization.
    """
    if ctx.user.role != UserRole.ADMIN:
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
    return getattr(request.state, "org_id", request.client.host if request.client else "127.0.0.1")


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
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )

    token = auth_header.split(" ", 1)[1].strip()
    return _verify_supabase_token(token)


def _verify_supabase_token(token: str) -> dict:
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

    global _supabase_jwks_cache, _supabase_jwks_fetched_at
    now = time.monotonic()
    if _supabase_jwks_cache is None or (now - _supabase_jwks_fetched_at) > _JWKS_TTL_SECONDS:
        try:
            headers = {}
            if SUPABASE_ANON_KEY:
                headers["apikey"] = SUPABASE_ANON_KEY
            resp = httpx.get(SUPABASE_JWKS_URL, headers=headers, timeout=5.0)
            resp.raise_for_status()
            _supabase_jwks_cache = resp.json()
            _supabase_jwks_fetched_at = now
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to fetch Supabase JWKS.",
            )

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


async def _build_supabase_auth_context(claims: dict, db: AsyncSession) -> SupabaseAuthContext:
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
        return SupabaseAuthContext(org_id=user.org_id, user=user, claims=claims)

    # No user by sub. Maybe they exist by email (e.g. legacy signup or another Supabase link).
    # Link this Supabase account to the existing user to avoid duplicate key on email.
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        user.supabase_user_id = sub
        await db.commit()
        await db.refresh(user)
        return SupabaseAuthContext(org_id=user.org_id, user=user, claims=claims)

    # First time we see this Supabase user. Check for a pending invite (magic-link flow).
    now = datetime.now(timezone.utc)
    stmt = (
        select(Invite)
        .where(
            Invite.email == email,
            Invite.is_accepted == False,
            Invite.expires_at > now,
        )
        .order_by(Invite.created_at.desc())
        .limit(1)
    )
    invite_result = await db.execute(stmt)
    invite = invite_result.scalar_one_or_none()

    if invite:
        # Join existing org as MEMBER.
        org_id = invite.org_id
        user = User(
            email=email,
            supabase_user_id=sub,
            hashed_password="!",
            org_id=org_id,
            role=UserRole.MEMBER,
        )
        db.add(user)
        invite.is_accepted = True
        await db.commit()
        await db.refresh(user)
        return SupabaseAuthContext(org_id=org_id, user=user, claims=claims)

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


async def get_supabase_auth_context(
    claims: dict = Depends(get_supabase_claims),
    db: AsyncSession = Depends(get_db),
) -> SupabaseAuthContext:
    """
    Maps a verified Supabase JWT to a local User + Organization.
    """
    return await _build_supabase_auth_context(claims, db)


async def _get_auth_context_from_key(
    api_key_value: str, request: Request, db: AsyncSession
) -> AuthContext:
    """Validates API key and returns AuthContext; raises HTTPException on failure."""
    hashed_key = hash_api_key(api_key_value)
    stmt = (
        select(ApiKey, User)
        .join(User, ApiKey.user_id == User.id)
        .where(ApiKey.key_hash == hashed_key, ApiKey.is_active == True)
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
) -> str:
    """
    Returns org_id from either Authorization: Bearer <Supabase JWT> or X-API-Key.
    Use this on routes that should accept both the Streamlit UI (Bearer) and API clients (API key).
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        claims = _verify_supabase_token(token)
        ctx = await _build_supabase_auth_context(claims, db)
        request.state.org_id = ctx.org_id
        return ctx.org_id
    if x_api_key:
        ctx = await _get_auth_context_from_key(x_api_key, request, db)
        return ctx.org_id
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
) -> SupabaseAuthContext:
    """Requires the authenticated Supabase user to be an ADMIN in our app."""
    if ctx.user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return ctx
