from qdrant_client import QdrantClient
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize Qdrant Client (adjust URL/API Key if using Cloud)
client = QdrantClient(url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY"))

COLLECTION_NAME = "legal_chunks" # Change this to your actual collection name

def confirm_metadata():
    print(f"--- Checking Collection: {COLLECTION_NAME} ---")
    
    # Scroll through the first 5 points to see their payload
    points, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        limit=5,
        with_payload=True,
        with_vectors=False
    )
    
    if not points:
        print("❌ Collection is empty!")
        return

    for i, point in enumerate(points):
        print(f"\n[Point {i+1}] Payload Structure:")
        for key, value in point.payload.items():
            print(f"  -> {key}: {value}")

if __name__ == "__main__":
    confirm_metadata()