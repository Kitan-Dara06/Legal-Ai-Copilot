import asyncio
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models import Organization

async def test():
    print("Testing DB connection...")
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Organization).limit(1))
        org = res.scalar_one_or_none()
        print(f"Org: {org}")

if __name__ == "__main__":
    asyncio.run(test())
