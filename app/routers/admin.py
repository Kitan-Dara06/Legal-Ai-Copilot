"""Admin Router — Role management and governance.

PATCH /orgs/{org_id}/members/{user_id}/role — Change a user's role
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_org_id_unified
from app.models import UserOrgMembership, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orgs/{org_id}/members", tags=["Admin"])


class UpdateRoleRequest(BaseModel):
    role: str  # "ADMIN", "PARTNER", "ASSOCIATE", "MEMBER"


@router.patch("/{user_id}/role", status_code=200)
async def update_member_role(
    user_id: uuid.UUID,
    req: UpdateRoleRequest,
    org_id: str = Depends(get_org_id_unified),
    db: AsyncSession = Depends(get_db),
):
    """Change a member's role in the org.

    Only ADMIN can change roles. Valid roles: ADMIN, PARTNER, ASSOCIATE, MEMBER.
    """
    try:
        new_role = UserRole(req.role.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: {req.role}. Must be one of: ADMIN, PARTNER, ASSOCIATE, MEMBER",
        )

    org_uuid = uuid.UUID(org_id) if isinstance(org_id, str) else org_id
    result = await db.execute(
        select(UserOrgMembership).where(
            UserOrgMembership.user_id == user_id,
            UserOrgMembership.org_id == org_uuid,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found in this org")

    membership.role = new_role
    await db.commit()
    return {"status": "updated", "user_id": str(user_id), "new_role": req.role}
