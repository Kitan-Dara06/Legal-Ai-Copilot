import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import (
    AuthContext,
    get_admin_auth_context,
)
from app.models import Invite, Organization, User, UserOrgMembership, UserRole
from passlib.context import CryptContext

router = APIRouter(tags=["invites"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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
async def generate_invite(
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
            detail="You can only invite users to your own organization."
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

    # TODO: In a real app, trigger Celery task to send email with raw_token
    # send_invite_email.delay(payload.email, raw_token, org_id)

    return {
        "message": "Invite generated successfully.",
        "raw_token": raw_token, # Returning raw_token for now so it's visible in tests
        "expires_at": new_invite.expires_at
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
            Invite.expires_at > datetime.now(timezone.utc)
        )
    )
    result = await db.execute(stmt)
    row = result.first()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation token."
        )
    
    invite, org = row
    return {
        "email": invite.email,
        "org_name": org.name or org.slug,
        "org_id": str(org.id),
        "role": invite.role
    }

@router.post("/invites/accept")
async def accept_invite(
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
        Invite.expires_at > datetime.now(timezone.utc)
    )
    invite = (await db.execute(stmt)).scalar_one_or_none()
    
    if not invite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired invitation token."
        )

    # Check if user already exists
    stmt = select(User).where(User.email == invite.email)
    existing_user = (await db.execute(stmt)).scalar_one_or_none()

    if existing_user:
        # User already exists, verify their password
        if not pwd_context.verify(payload.password, existing_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User with this email already exists. Please enter your correct existing password to accept the invite."
            )
        target_user = existing_user
    else:
        # Create new user
        new_user = User(
            email=invite.email,
            full_name=payload.full_name,
            hashed_password=pwd_context.hash(payload.password),
            org_id=invite.org_id,        # Set their primary org
            personal_org_id=invite.org_id,
            role=invite.role,
        )
        db.add(new_user)
        target_user = new_user

    async with db.begin_nested(): # Transaction starts
        if not existing_user:
            await db.flush() # Get the new_user.id
            
        # Create the row in user_org_memberships if it doesn't exist
        stmt_mem = select(UserOrgMembership).where(
            UserOrgMembership.user_id == target_user.id,
            UserOrgMembership.org_id == invite.org_id
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
    
    # Generate and return JWT
    import os
    from jose import jwt as jose_jwt
    
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET")
    if not jwt_secret:
        jwt_secret = "placeholder_secret"
    
    now = datetime.now(timezone.utc)
    payload_jwt = {
        "sub": str(target_user.id),
        "email": target_user.email,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=24)).timestamp()),
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {"full_name": payload.full_name if not existing_user else target_user.full_name},
    }
    
    token = jose_jwt.encode(payload_jwt, jwt_secret, algorithm="HS256")
    
    return {
        "message": "Invitation accepted successfully.",
        "user_id": str(target_user.id),
        "org_id": str(invite.org_id),
        "access_token": token,
    }
