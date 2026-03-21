import os
from qdrant_client import QdrantClient
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

def reset_qdrant():
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    
    collection_name = "legal_chunks"
    print(f"🔄 Wiping collection: {collection_name}")
    
    try:
        client.delete_collection(collection_name)
        print(f"✅ Deleted {collection_name}")
    except Exception as e:
        print(f"⚠️ Could not delete (maybe it doesn't exist): {e}")
        
    print(f"✨ Reset complete. The next file upload will recreate the collection with the correct 'dense' vector schema.")

if __name__ == "__main__":
    reset_qdrant()
