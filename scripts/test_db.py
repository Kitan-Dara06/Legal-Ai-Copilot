import asyncio
import os
import sys
from sqlalchemy import text

sys.path.insert(0, os.getcwd())
from app.database import AsyncSessionLocal

async def test():
    print("Testing DB connection...")
    try:
        async with AsyncSessionLocal() as db:
            res = await db.execute(text("SELECT 1"))
            print(f"Result: {res.scalar()}")
            print("✅ DB connection successful")
    except Exception as e:
        print(f"❌ DB connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
