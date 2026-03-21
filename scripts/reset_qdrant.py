import os, asyncio, sys
from qdrant_client import QdrantClient
from dotenv import load_dotenv
from sqlalchemy import text

# Force current directory into path so 'app' is findable
sys.path.append(os.getcwd())

from app.database import engine

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

async def wipe_postgres():
    print("🐘 Wiping PostgreSQL 'files' table...")
    async with engine.begin() as conn:
        # We use a raw SQL TRUNCATE or DELETE to clear the metadata
        await conn.execute(text("DELETE FROM files CASCADE"))
    print("✅ PostgreSQL 'files' table cleared.")

def wipe_qdrant():
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    collection_name = "legal_chunks"
    print(f"🔄 Wiping Qdrant collection: {collection_name}")
    try:
        client.delete_collection(collection_name)
        print(f"✅ Deleted {collection_name}")
    except Exception as e:
        print(f"⚠️ Could not delete Qdrant collection: {e}")

async def main():
    wipe_qdrant()
    await wipe_postgres()
    print("\n✨ GLOBAL RESET COMPLETE.")
    print("All file metadata and vector embeddings have been removed.")
    print("You can now upload your files fresh! 🚀")

if __name__ == "__main__":
    asyncio.run(main())
