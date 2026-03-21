import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import AuthApiError
from supabase import create_client as create_supabase_client

from app.database import get_db
from app.dependencies import (
    AuthContext,
    get_admin_auth_context,
    get_supabase_admin_context,
    get_supabase_auth_context,
    get_supabase_claims,
    hash_api_key,
)
from app.models import ApiKey, Invite, Organization, User, UserOrgMembership, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
limiter = Limiter(key_func=get_remote_address)


# ── Pydantic Schemas ──────────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    org_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{2,50}$")
    org_name: str | None = Field(None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)


class InviteRequest(BaseModel):
    email: EmailStr


class SetupOrgRequest(BaseModel):
    org_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{2,50}$")
    org_name: str | None = Field(None, max_length=255)


class AcceptInviteRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
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

logger = logging.getLogger(__name__)


@router.get("/check-org")
async def check_org(
    org_id: str = Query(..., min_length=3, max_length=50),
    db: AsyncSession = Depends(get_db)
):
    """
    Checks if an organization slug is available before proceeding with user signup.
    Returns 200 { "available": bool }
    """
    stmt = select(Organization).where(Organization.slug == org_id.lower())
    existing = (await db.execute(stmt)).scalar_one_or_none()
    return {"available": existing is None}


@router.post("/signup", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def signup(
    request: Request, payload: SignupRequest, db: AsyncSession = Depends(get_db)
):
    """
    Creates a new organization, admin user, and initial API key.
    Fails if the org_id already exists (to prevent org hijacking).
    """
    # 1. Check if user already exists first
    stmt = select(User).where(User.email == payload.email)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User already exists.")

    # 2. Check if Org slug already exists
    stmt = select(Organization).where(Organization.slug == payload.org_id)
    org = (await db.execute(stmt)).scalar_one_or_none()

    if org:
        raise HTTPException(
            status_code=400,
            detail="Organization already exists. Choose a different workspace slug or ask for an invite.",
        )

    # Extra safety: bcrypt has a 72-byte limit on the *encoded* password.
    if len(payload.password.encode("utf-8")) > 72:
        logger.warning("Signup rejected: password too long for email=%s", payload.email)
        raise HTTPException(
            status_code=400,
            detail="Password must be at most 72 characters long.",
        )

    # 1. Create Organization (slug = human-readable org_id; UUID internal id)
    new_org = Organization(slug=payload.org_id)
    db.add(new_org)
    await db.flush()

    # 2. Create Admin User with personal_org_id + membership
    new_user = User(
        email=payload.email,
        hashed_password=pwd_context.hash(payload.password),
        org_id=new_org.id,
        personal_org_id=new_org.id,
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

    db.add(
        UserOrgMembership(
            user_id=new_user.id,
            org_id=new_org.id,
            role=UserRole.ADMIN,
        )
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
async def login(
    request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)
):
    """
    Authenticates a user and issues a NEW API key.
    """
    stmt = (
        select(User, Organization)
        .join(Organization, User.org_id == Organization.id)
        .where(User.email == payload.email)
    )
    row = (await db.execute(stmt)).first()
    if not row:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )
    user, org = row
    if not pwd_context.verify(payload.password, user.hashed_password):
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
        "org_id": str(user.org_id),
        "org_slug": org.slug if org else None,
    }


@router.post(
    "/setup-org",
    status_code=status.HTTP_201_CREATED,
    summary="Initialize User Workspace",
    response_description="Binds a Supabase user to a new internal organization.",
)
@limiter.limit("10/minute")
async def setup_org(
    request: Request,
    payload: SetupOrgRequest,
    claims: dict = Depends(get_supabase_claims),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates an organization for an authenticated Supabase user and sets them as ADMIN.
    Intended for users who passed Supabase auth but have no local workspace yet.
    """
    sub = claims.get("sub")
    email = claims.get("email")
    logger.info("[setup_org] Starting setup for email=%s sub=%s", email, sub)

    if not sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supabase token missing required claims.",
        )

    org_id = (payload.org_id or "").strip().lower()
    logger.info("[setup_org] Requested org_slug=%s", org_id)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="org_id is required.",
        )

    existing_org = (
        await db.execute(select(Organization).where(Organization.slug == org_id))
    ).scalar_one_or_none()
    if existing_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization already exists. Choose a different org_id.",
        )

    user_by_sub = (
        await db.execute(select(User).where(User.supabase_user_id == sub))
    ).scalar_one_or_none()
    if user_by_sub:
        logger.info(
            "[setup_org] Found user by sub. Current org_id=%s", user_by_sub.org_id
        )
    if user_by_sub and user_by_sub.org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is already linked to an organization.",
        )

    user_by_email = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user_by_email:
        logger.info(
            "[setup_org] Found user by email. Current org_id=%s", user_by_email.org_id
        )
    if user_by_email and user_by_email.org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email is already linked to an organization.",
        )

    new_org = Organization(slug=org_id, name=payload.org_name or org_id)
    db.add(new_org)
    await db.flush()
    logger.info(
        "[setup_org] Created new organization. internal_id=%s slug=%s",
        new_org.id,
        new_org.slug,
    )

    if user_by_sub:
        logger.info("[setup_org] Linking new org to existing user_by_sub")
        user = user_by_sub
    elif user_by_email:
        logger.info("[setup_org] Linking new org to existing user_by_email")
        user = user_by_email
    else:
        logger.info("[setup_org] Creating entirely new local user record")
        user = User(
            email=email,
            supabase_user_id=sub,
            hashed_password="!",
            org_id=new_org.id,
            personal_org_id=new_org.id,
            role=UserRole.ADMIN,
        )
        db.add(user)
        await db.flush()
        db.add(
            UserOrgMembership(user_id=user.id, org_id=new_org.id, role=UserRole.ADMIN)
        )
        await db.commit()
        await db.refresh(user)
        return {
            "message": "Organization created and account linked.",
            "org_id": str(new_org.id),
            "org_slug": new_org.slug,
            "org_name": new_org.name,
            "email": user.email,
            "app_role": user.role.value,
        }

    user.supabase_user_id = user.supabase_user_id or sub
    user.org_id = new_org.id
    user.personal_org_id = new_org.id
    user.role = UserRole.ADMIN
    await db.flush()
    logger.info("[setup_org] Updated user record with new org_id=%s", new_org.id)
    db.add(UserOrgMembership(user_id=user.id, org_id=new_org.id, role=UserRole.ADMIN))
    await db.commit()
    await db.refresh(user)

    return {
        "message": "Organization created and account linked.",
        "org_id": str(new_org.id),
        "org_slug": new_org.slug,
        "org_name": new_org.name,
        "email": user.email,
        "app_role": user.role.value,
    }


@router.get("/my-orgs")
async def my_orgs(
    ctx=Depends(get_supabase_auth_context), db: AsyncSession = Depends(get_db)
):
    """
    Returns all orgs the authenticated user belongs to with their roles.
    """
    rows = (
        await db.execute(
            select(Organization.slug, Organization.id, UserOrgMembership.role)
            .join(UserOrgMembership, UserOrgMembership.org_id == Organization.id)
            .where(UserOrgMembership.user_id == ctx.user.id)
        )
    ).all()
    return {
        "orgs": [
            {"org_id": str(r.id), "org_slug": r.slug, "role": r.role.value}
            for r in rows
        ]
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


@router.post(
    "/invite-by-email",
    summary="Send Magic Link Invite",
    response_description="Dispatches a Supabase Magic Link to add a coworker to your organization.",
)
async def invite_user_by_email(
    request: Request,
    payload: InviteRequest,
    ctx=Depends(get_supabase_admin_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Admin Only, Supabase JWT) Creates an invite and sends a magic link email via Supabase.
    The recipient signs in via the link and is added to your organization as a member.
    """
    logger.info(
        "[invite_user_by_email] Called by user: %s, org_id: %s, role: %s",
        ctx.user.email,
        ctx.org_id,
        ctx.user.role,
    )
    logger.debug("[invite_user_by_email] Headers: %s", dict(request.headers))

    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not service_role_key:
        logger.warning("SUPABASE_SERVICE_ROLE_KEY not set; cannot send invite email")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invite-by-email not configured. Set SUPABASE_SERVICE_ROLE_KEY.",
        )

    import hashlib
    invite_token = secrets.token_urlsafe(32)
    hashed_token = hashlib.sha256(invite_token.encode("utf-8")).hexdigest()
    
    new_invite = Invite(
        email=payload.email,
        org_id=ctx.org_id,
        token=hashed_token,
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
            options={"redirect_to": f"{frontend_url}/login?type=recovery"},
        )
    except AuthApiError as e:
        # Already registered in Supabase: still add them via invite link (they accept in-app).
        if "already been registered" in (str(e).lower() or ""):
            invite_link = f"{frontend_url}/invite?token={invite_token}"
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
    import hashlib
    hashed_token = hashlib.sha256(token.encode("utf-8")).hexdigest()

    stmt = select(Invite).where(
        Invite.token == hashed_token,
        Invite.is_accepted.is_(False),
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
    import hashlib
    hashed_token = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()

    stmt = select(Invite).where(
        Invite.token == hashed_token,
        Invite.is_accepted.is_(False),
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
    ctx.user.personal_org_id = ctx.user.personal_org_id or invite.org_id
    invite.is_accepted = True
    await db.flush()
    db.add(
        UserOrgMembership(
            user_id=ctx.user.id, org_id=invite.org_id, role=UserRole.MEMBER
        )
    )
    await db.commit()
    await db.refresh(ctx.user)
    return {
        "message": "You have joined the organization.",
        "org_id": str(invite.org_id),
    }


@router.post("/accept-invite")
async def accept_invite(
    payload: AcceptInviteRequest, db: AsyncSession = Depends(get_db)
):
    """
    Consumes an invite token, creates a MEMBER user, and issues an API key.
    """
    import hashlib
    hashed_token = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()

    stmt = select(Invite).where(
        Invite.token == hashed_token,
        Invite.email == payload.email,
        Invite.is_accepted.is_(False),
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
        personal_org_id=invite.org_id,
        role=UserRole.MEMBER,
    )
    db.add(new_user)

    # 2. Mark Invite Accepted + membership
    invite.is_accepted = True
    await db.flush()
    db.add(
        UserOrgMembership(
            user_id=new_user.id, org_id=invite.org_id, role=UserRole.MEMBER
        )
    )

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
        "org_id": str(invite.org_id),
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


@router.get(
    "/members",
    summary="List Organization Members",
    response_description="Returns all users currently attached to your workspace.",
)
async def my_org_members(
    ctx: AuthContext = Depends(get_supabase_admin_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Admin Only) Returns a list of all members in the current organization.
    """
    stmt = (
        select(
            User.id,
            User.email,
            User.full_name,
            UserOrgMembership.role,
            UserOrgMembership.created_at,
        )
        .join(UserOrgMembership, User.id == UserOrgMembership.user_id)
        .where(UserOrgMembership.org_id == ctx.org_id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "user_id": str(row.id),
            "email": row.email,
            "full_name": row.full_name,
            "role": row.role,
            "joined_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.delete("/members/{user_id}")
async def remove_member(
    user_id: str,
    ctx: AuthContext = Depends(get_supabase_admin_context),
    db: AsyncSession = Depends(get_db),
):
    """
    (Admin Only) Removes a user from the organization.
    """
    from uuid import UUID

    target_user_id = UUID(user_id)

    if target_user_id == ctx.user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot remove yourself from the organization.",
        )

    stmt = select(UserOrgMembership).where(
        UserOrgMembership.user_id == target_user_id,
        UserOrgMembership.org_id == ctx.org_id,
    )
    membership = (await db.execute(stmt)).scalar_one_or_none()

    if not membership:
        raise HTTPException(
            status_code=404,
            detail="Member not found in your organization.",
        )

    await db.delete(membership)
    await db.commit()

    return {"message": "Member removed successfully."}


@router.get(
    "/me",
    summary="Get Current User Identity",
    response_description="Returns the user's role and their active organization ID.",
)
async def who_am_i(
    ctx=Depends(get_supabase_auth_context), db: AsyncSession = Depends(get_db)
):
    """
    Supabase-authenticated endpoint. Returns identity, org_id, org_slug, and app role for the UI.
    """
    org_slug = None
    org_name = None
    try:
        if ctx.org_id:
            org_res = await db.execute(
                select(Organization).where(Organization.id == ctx.org_id)
            )
            org = org_res.scalar_one_or_none()
            if org:
                org_slug = org.slug
                org_name = org.name
    except Exception:
        org_slug = None
        org_name = None

    return {
        "sub": ctx.claims.get("sub"),
        "email": ctx.claims.get("email"),
        "role": ctx.claims.get("role"),
        "aud": ctx.claims.get("aud"),
        "org_id": ctx.org_id,
        "org_slug": org_slug,
        "org_name": org_name,
        "app_role": ctx.user.role.value,
    }


@router.post("/logout")
async def logout():
    """
    Clears the session cookies for the frontend.
    The frontend should also call supabase.auth.signOut().
    """
    response = JSONResponse(content={"message": "Logged out successfully."})
    # List of common Supabase/Auth cookies to clear
    cookies_to_clear = [
        "sb-access-token",
        "sb-refresh-token",
        "supabase-auth-token",
    ]
    for cookie in cookies_to_clear:
        response.delete_cookie(cookie)
    return response


@router.delete("/debug/cleanup-user/{email}")
async def debug_cleanup_user(
    email: str,
    db: AsyncSession = Depends(get_db),
):
    """
    (DEBUG ONLY) Completely removes a user and their memberships from the local DB.
    Use this to fix organization crosstalk issues after deleting a user from Supabase.
    """
    from sqlalchemy import delete

    # Find the user
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found in local database.")

    # Delete memberships
    await db.execute(
        delete(UserOrgMembership).where(UserOrgMembership.user_id == user.id)
    )
    # Delete API keys
    await db.execute(delete(ApiKey).where(ApiKey.user_id == user.id))
    # Delete the user record
    await db.execute(delete(User).where(User.id == user.id))

    await db.commit()
    return {
        "message": f"User {email} and all associations cleared from local database."
    }


@router.get("/my-orgs")
async def list_my_orgs(
    ctx=Depends(get_supabase_auth_context), db: AsyncSession = Depends(get_db)
):
    """
    Returns all organizations the current user is a member of.
    """
    stmt = (
        select(
            Organization.id,
            Organization.slug,
            Organization.name,
            UserOrgMembership.role,
        )
        .join(UserOrgMembership, Organization.id == UserOrgMembership.org_id)
        .where(UserOrgMembership.user_id == ctx.user.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "org_id": str(row.id),
            "org_slug": row.slug,
            "org_name": row.name,
            "role": row.role.value,
            "is_active": str(row.id) == str(ctx.org_id),
        }
        for row in rows
    ]
