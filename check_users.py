import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import User, UserOrgMembership

DATABASE_URL = "postgresql+asyncpg://postgres.mluvydatmokiqmwyqcwb:F%21f7vpMp.2JgjUm@aws-1-eu-west-1.pooler.supabase.com:5432/postgres"

async def main():
    engine = create_async_engine(DATABASE_URL)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        res = await db.execute(select(User))
        users = res.scalars().all()
        print(f"Found {len(users)} users:")
        for u in users:
            print(f"ID: {u.id}, Email: {u.email}, Role: {u.role}")
            
        res_mem = await db.execute(select(UserOrgMembership))
        mems = res_mem.scalars().all()
        print(f"\nFound {len(mems)} memberships:")
        for m in mems:
            print(f"User ID: {m.user_id}, Org ID: {m.org_id}, Role: {m.role}")

if __name__ == "__main__":
    asyncio.run(main())
