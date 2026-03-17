import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

client = QdrantClient(
    url=os.getenv("QDRANT_URL", "http://localhost:6333"),
    api_key=os.getenv("QDRANT_API_KEY")
)

COLLECTION_NAME = "legal_chunks"

def check_non_compete():
    print(f"--- Searching for 'Non-Compete' in {COLLECTION_NAME} ---")
    
    try:
        points, next_page = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            with_payload=True,
            with_vectors=False
        )
        
        found_chunks = []
        for point in points:
            # CORRECT PAYLOAD FIELDS
            chunk_txt = point.payload.get('chunk_text', '')
            section_txt = point.payload.get('section_text', '')
            content = chunk_txt + " " + section_txt
            
            if "non-compete" in content.lower() or "covenant period" in content.lower() or "non-competition" in content.lower():
                found_chunks.append({
                    "id": point.id,
                    "filename": point.payload.get("filename", "Unknown"),
                    "page": point.payload.get("page_number", "Unknown"),
                    "content_snippet": chunk_txt[:200] + "..." if len(chunk_txt) > 200 else chunk_txt
                })
        
        if found_chunks:
            print(f"✅ Found {len(found_chunks)} chunks containing 'Non-Compete' related terms:\n")
            for idx, chunk in enumerate(found_chunks):
                print(f"--- Match {idx+1} ---")
                print(f"ID: {chunk['id']}")
                print(f"Source: {chunk['filename']} (Page {chunk['page']})")
                print(f"Snippet: {chunk['content_snippet']}\n")
        else:
            print("❌ No 'Non-Compete' chunks found in the database. They might not have been indexed.")
            
    except Exception as e:
        print(f"Error checking Qdrant: {e}")

if __name__ == "__main__":
    check_non_compete()
