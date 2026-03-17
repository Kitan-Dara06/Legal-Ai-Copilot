import os
import httpx
from dotenv import load_dotenv
from app.services.embedder import get_embedding

load_dotenv()

def test_cloudflare_integration():
    texts = ["This is a test document.", "Legal RAG systems are cool."]
    
    print("Testing get_embedding with Cloudflare prioritized...")
    try:
        embeddings = get_embedding(texts)
        print(f"✅ Successfully retrieved {len(embeddings)} embeddings.")
        print(f"✅ Embedding dimension: {len(embeddings[0])}")
        
    except Exception as e:
        print(f"❌ Failed to get embeddings: {e}")

if __name__ == "__main__":
    test_cloudflare_integration()
