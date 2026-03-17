import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from supabase import AuthApiError, create_client as create_supabase_client
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, constr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    AuthContext,
    get_admin_auth_context,
    get_supabase_admin_context,
    get_supabase_auth_context,
    get_supabase_claims,
    hash_api_key,
)
from app.models import ApiKey, Invite, Organization, User, UserRole
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
limiter = Limiter(key_func=get_remote_address)


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=72)
    org_id: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=72)


class InviteRequest(BaseModel):
    email: EmailStr


class AcceptInviteRequest(BaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=72)
    token: str


class AcceptInviteByTokenRequest(BaseModel):
    token: str


class RevokeRequest(BaseModel):
    prefix: str


# ── Helper ────────────────────────────────────────────────────────────────────


def generate_api_key() -> tuple[str, str, str]:
    """Generates a secure key, returns: (raw_key, prefix, hashed_key)"""
    raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
    prefix = raw_key[:12]
    hashed_key = hash_api_key(raw_key)
    return raw_key, prefix, hashed_key


# ── Endpoints ─────────────────────────────────────────────────────────────────

import logging

logger = logging.getLogger(__name__)

@router.post("/signup", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def signup(request: Request, payload: SignupRequest, db: AsyncSession = Depends(get_db)):
    """
    Creates a new organization, admin user, and initial API key.
    Fails if the org_id already exists (to prevent org hijacking).
    """
    stmt = select(Organization).where(Organization.id == payload.org_id)
    org = (await db.execute(stmt)).scalar_one_or_none()

    # Extra safety: bcrypt has a 72-byte limit on the *encoded* password.
    if len(payload.password.encode("utf-8")) > 72:
        logger.warning(
            "Signup rejected: password too long for email=%s", payload.email
        )
        raise HTTPException(
            status_code=400,
            detail="Password must be at most 72 characters long.",
        )
    if org:
        raise HTTPException(
            status_code=400,
            detail="Organization already exists. You must be invited to join it.",
        )

    stmt = select(User).where(User.email == payload.email)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already exists.")

    # 1. Create Organization
    new_org = Organization(id=payload.org_id)
    db.add(new_org)

    # 2. Create Admin User
    new_user = User(
        email=payload.email,
        hashed_password=pwd_context.hash(payload.password),
        org_id=payload.org_id,
        role=UserRole.ADMIN,
    )
    db.add(new_user)
    try:
        await db.flush()  # So new_user gets its UUID; may raise if org/user duplicate
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Organization already exists. You must be invited to join it.",
        )

    # 3. Create API Key
    raw_key, prefix, hashed_key = generate_api_key()
    new_key = ApiKey(
        prefix=prefix,
        key_hash=hashed_key,
        user_id=new_user.id,
        org_id=new_org.id,
    )
    db.add(new_key)
    await db.commit()

    return {
        "message": "Admin user created.",
        "api_key": raw_key,
        "prefix": prefix,
        "org_id": new_org.id,
    }


@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticates a user and issues a NEW API key.
    """
    stmt = select(User).where(User.email == payload.email)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user or not pwd_context.verify(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    raw_key, prefix, hashed_key = generate_api_key()
    new_key = ApiKey(
        prefix=prefix,
        key_hash=hashed_key,
        user_id=user.id,
        org_id=user.org_id,
    )
    db.add(new_key)
    await db.commit()

    return {
        "message": "Login successful. New API key issued.",
        "api_key": raw_key,
        "prefix": prefix,
        "org_id": user.org_id,
    }


@router.post("/invite")
async def invite_user(
    payload: InviteRequest,
    ctx: AuthContext = Depends(get_admin_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Admin Only, API Key) Generates a secure invite token for a coworker to join the organization.
    """
    invite_token = secrets.token_urlsafe(32)
    new_invite = Invite(
        email=payload.email,
        org_id=ctx.org_id,
        token=invite_token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(new_invite)
    await db.commit()

    return {
        "message": "Invite created successfully.",
        "invite_token": invite_token,
    }


@router.post("/invite-by-email")
async def invite_user_by_email(
    payload: InviteRequest,
    ctx=Depends(get_supabase_admin_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Admin Only, Supabase JWT) Creates an invite and sends a magic link email via Supabase.
    The recipient signs in via the link and is added to your organization as a member.
    """
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not service_role_key:
        logger.warning("SUPABASE_SERVICE_ROLE_KEY not set; cannot send invite email")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invite-by-email not configured. Set SUPABASE_SERVICE_ROLE_KEY.",
        )

    invite_token = secrets.token_urlsafe(32)
    new_invite = Invite(
        email=payload.email,
        org_id=ctx.org_id,
        token=invite_token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(new_invite)
    await db.commit()

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8501").rstrip("/")
    if not supabase_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SUPABASE_URL not set.",
        )

    try:
        admin_client = create_supabase_client(supabase_url, service_role_key)
        admin_client.auth.admin.invite_user_by_email(
            payload.email,
            options={"redirect_to": frontend_url},
        )
    except AuthApiError as e:
        # Already registered in Supabase: still add them via invite link (they accept in-app).
        if "already been registered" in (str(e).lower() or ""):
            invite_link = f"{frontend_url}?invite_token={new_invite.token}"
            return {
                "message": "This email already has an account. Share the link below so they can join your organization.",
                "invite_link": invite_link,
                "already_registered": True,
            }
        logger.exception("Supabase invite_user_by_email failed for %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send invite email.",
        ) from e
    except Exception as e:
        logger.exception("Supabase invite_user_by_email failed for %s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send invite email. They may already have an account.",
        ) from e

    return {
        "message": "Invite sent. They will receive an email from Supabase with a link to sign in and join your organization.",
    }


@router.get("/invite-info")
async def invite_info(
    token: str = Query(..., alias="token"),
    db: AsyncSession = Depends(get_db),
):
    """
    Public: returns invite details for a token so the frontend can show "Invited to join org X".
    """
    stmt = select(Invite).where(
        Invite.token == token,
        Invite.is_accepted == False,
        Invite.expires_at > datetime.now(timezone.utc),
    )
    invite = (await db.execute(stmt)).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite.")
    return {"email": invite.email, "org_id": invite.org_id, "valid": True}


@router.post("/accept-invite-by-token")
async def accept_invite_by_token(
    payload: AcceptInviteByTokenRequest,
    ctx=Depends(get_supabase_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Supabase JWT) Accept an invite and switch to the inviter's organization.
    Invite email must match the logged-in user's email.
    """
    stmt = select(Invite).where(
        Invite.token == payload.token,
        Invite.is_accepted == False,
        Invite.expires_at > datetime.now(timezone.utc),
    )
    invite = (await db.execute(stmt)).scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite.")
    if invite.email.lower() != (ctx.claims.get("email") or "").lower():
        raise HTTPException(
            status_code=403,
            detail="This invite was sent to a different email address.",
        )
    ctx.user.org_id = invite.org_id
    ctx.user.role = UserRole.MEMBER  # Joining via invite is always as member, not admin
    invite.is_accepted = True
    await db.commit()
    await db.refresh(ctx.user)
    return {
        "message": "You have joined the organization.",
        "org_id": invite.org_id,
    }


@router.post("/accept-invite")
async def accept_invite(
    payload: AcceptInviteRequest, db: AsyncSession = Depends(get_db)
):
    """
    Consumes an invite token, creates a MEMBER user, and issues an API key.
    """
    stmt = select(Invite).where(
        Invite.token == payload.token,
        Invite.email == payload.email,
        Invite.is_accepted == False,
        Invite.expires_at > datetime.now(timezone.utc),
    )
    invite = (await db.execute(stmt)).scalar_one_or_none()

    if not invite:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired invite token.",
        )

    stmt = select(User).where(User.email == payload.email)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already exists.")

    # 1. Create Member User
    new_user = User(
        email=payload.email,
        hashed_password=pwd_context.hash(payload.password),
        org_id=invite.org_id,
        role=UserRole.MEMBER,
    )
    db.add(new_user)

    # 2. Mark Invite Accepted
    invite.is_accepted = True
    await db.flush()

    # 3. Issue API Key
    raw_key, prefix, hashed_key = generate_api_key()
    new_key = ApiKey(
        prefix=prefix,
        key_hash=hashed_key,
        user_id=new_user.id,
        org_id=invite.org_id,
    )
    db.add(new_key)
    await db.commit()

    return {
        "message": "Invite accepted. User created.",
        "api_key": raw_key,
        "prefix": prefix,
        "org_id": invite.org_id,
    }


@router.post("/revoke")
async def revoke_key(
    payload: RevokeRequest,
    ctx: AuthContext = Depends(get_admin_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Admin Only) Disables an API key by its prefix for the current organization.
    """
    stmt = select(ApiKey).where(
        ApiKey.prefix == payload.prefix,
        ApiKey.org_id == ctx.org_id,
    )
    key_record = (await db.execute(stmt)).scalar_one_or_none()

    if not key_record:
        raise HTTPException(
            status_code=404,
            detail="API Key not found or does not belong to your organization.",
        )

    key_record.is_active = False
    await db.commit()

    return {"message": f"API Key starting with '{payload.prefix}' has been revoked."}


@router.get("/me")
async def who_am_i(ctx=Depends(get_supabase_auth_context)):
    """
    Supabase-authenticated endpoint. Returns identity, org_id, and app role for the UI.
    """
    return {
        "sub": ctx.claims.get("sub"),
        "email": ctx.claims.get("email"),
        "role": ctx.claims.get("role"),
        "aud": ctx.claims.get("aud"),
        "org_id": ctx.org_id,
        "app_role": ctx.user.role.value,
    }
