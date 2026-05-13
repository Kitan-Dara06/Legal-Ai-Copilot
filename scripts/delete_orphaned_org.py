import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import User, Organization, UserOrgMembership

async def remove_orphaned_org():
    async with AsyncSessionLocal() as session:
        # Find the org
        stmt = select(Organization).where(Organization.slug == "love2000")
        org = (await session.execute(stmt)).scalar_one_or_none()
        
        if not org:
            print("Org 'love2000' not found.")
            return

        # Find users who belong to this org mainly
        stmt_users = select(User).where(User.org_id == org.id)
        users = (await session.execute(stmt_users)).scalars().all()

        for u in users:
            print(f"Deleting user: {u.email}")
            # Delete their memberships first
            await session.execute(
                UserOrgMembership.__table__.delete().where(UserOrgMembership.user_id == u.id)
            )
            await session.delete(u)

        print(f"Deleting org: love2000")
        await session.delete(org)
        await session.commit()
        print("Done!")

if __name__ == "__main__":
    asyncio.run(remove_orphaned_org())
