import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    AuthContext,
    get_admin_auth_context,
)
from app.models import Invite, Organization, User, UserOrgMembership, UserRole

router = APIRouter(tags=["invites"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
limiter = Limiter(key_func=get_remote_address)

# --- Pydantic Schemas ---


class CreateInviteRequest(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.MEMBER


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8, max_length=72)
    full_name: str = Field(..., min_length=1, max_length=255)


# --- Helpers ---


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- Endpoints ---


@router.post("/orgs/{org_id}/invites", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def generate_invite(
    request: Request,
    org_id: str,
    payload: CreateInviteRequest,
    ctx: AuthContext = Depends(get_admin_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint 1: Generate Invite
    Admin requests to invite a colleague. Backend generates a secure random token,
    saves a row to organization_invites (hashed), and fires off email (simulated here).
    """
    # Security: Ensure ctx.org_id matches requested org_id
    if str(ctx.org_id) != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only invite users to your own organization.",
        )

    raw_token = secrets.token_urlsafe(32)
    hashed_token = hash_token(raw_token)

    new_invite = Invite(
        email=payload.email,
        org_id=ctx.org_id,
        role=payload.role,
        token=hashed_token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(new_invite)
    await db.commit()

    # Dispatch Supabase Magic Link
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

    if supabase_url and service_role_key:
        try:
            from supabase import create_client as create_supabase_client

            admin_client = create_supabase_client(supabase_url, service_role_key)
            # The redirect_to should ideally point to our /invite page with the token
            # But Supabase's invite_user_by_email sends a magic link for Supabase auth.
            # We use type=recovery as a trick to allow password setting.
            admin_client.auth.admin.invite_user_by_email(
                payload.email,
                options={"redirect_to": f"{frontend_url}/invite?token={raw_token}"},
            )
            logging.getLogger(__name__).info(f"Supabase invite sent to {payload.email}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to send Supabase invite: {e}")
            # We don't fail the request here because the local invite is already created.
            # The admin can still manually share the link if needed.

    return {
        "message": "Invite generated successfully. If configured, an email has been sent.",
        "invite_link": f"{frontend_url}/invite?token={raw_token}",
        "expires_at": new_invite.expires_at,
    }


@router.get("/invites/verify")
async def verify_token(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint 2: Verify Token
    Checks if the token is valid and unexpired. Returns email and Organization Name.
    """
    hashed_token = hash_token(token)
    stmt = (
        select(Invite, Organization)
        .join(Organization, Invite.org_id == Organization.id)
        .where(
            Invite.token == hashed_token,
            Invite.is_accepted == False,
            Invite.expires_at > datetime.now(timezone.utc),
        )
    )
    result = await db.execute(stmt)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation token.",
        )

    invite, org = row
    return {
        "email": invite.email,
        "org_name": org.name or org.slug,
        "org_id": str(org.id),
        "role": invite.role,
    }


@router.post("/invites/accept")
@limiter.limit("5/minute")
async def accept_invite(
    request: Request,
    payload: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Endpoint 3: Accept & Consume (The Magic Transaction)
    Creates user (or verifies existing user), adds membership, marks invite accepted, and returns JWT.
    """
    hashed_token = hash_token(payload.token)
    stmt = select(Invite).where(
        Invite.token == hashed_token,
        Invite.is_accepted == False,
        Invite.expires_at > datetime.now(timezone.utc),
    )
    invite = (await db.execute(stmt)).scalar_one_or_none()

    if not invite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation token.",
        )

    # Check if user already exists
    stmt = select(User).where(User.email == invite.email)
    existing_user = (await db.execute(stmt)).scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "login_required",
                "message": "Invitation recognized. Please log in to continue.",
            },
        )
    else:
        # 1. Create the user in Supabase Auth first
        import os

        from supabase import create_client as create_supabase_client

        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        supabase_user_id = None

        if supabase_url and service_role_key:
            try:
                admin_client = create_supabase_client(supabase_url, service_role_key)
                res = admin_client.auth.admin.create_user(
                    {
                        "email": invite.email,
                        "password": payload.password,
                        "email_confirm": True,
                        "user_metadata": {"full_name": payload.full_name},
                    }
                )
                supabase_user_id = res.user.id
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    "Supabase user creation failed: %s", e
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create authentication profile. Please try again later.",
                )

        # 2. Create the user in our local Postgres database
        new_user = User(
            email=invite.email,
            supabase_user_id=supabase_user_id,
            full_name=payload.full_name,
            hashed_password=pwd_context.hash(payload.password),
            org_id=invite.org_id,  # Set their primary org
            personal_org_id=invite.org_id,
            role=invite.role,
        )
        db.add(new_user)
        target_user = new_user

    async with db.begin_nested():  # Transaction starts
        if not existing_user:
            await db.flush()  # Get the new_user.id

        # Create the row in user_org_memberships if it doesn't exist
        stmt_mem = select(UserOrgMembership).where(
            UserOrgMembership.user_id == target_user.id,
            UserOrgMembership.org_id == invite.org_id,
        )
        membership = (await db.execute(stmt_mem)).scalar_one_or_none()

        if not membership:
            membership = UserOrgMembership(
                user_id=target_user.id,
                org_id=invite.org_id,
                role=invite.role,
            )
            db.add(membership)

        # Mark the invite row as is_accepted = True
        invite.is_accepted = True

    await db.commit()

    # Return Supabase-login-compatible response instead of custom JWT
    return {
        "message": "Invitation accepted successfully. Please log in.",
        "redirect": f"{os.getenv('FRONTEND_URL', 'http://localhost:3000')}/login",
    }
