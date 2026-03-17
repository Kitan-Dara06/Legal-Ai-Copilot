import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models

load_dotenv()

client = QdrantClient(
    url=os.getenv("QDRANT_URL", "http://localhost:6333"),
    api_key=os.getenv("QDRANT_API_KEY")
)

COLLECTION_NAME = "legal_chunks"

def check_non_compete():
    print(f"--- Searching for 'Non-Compete' in {COLLECTION_NAME} ---")
    
    # Simple text search if the payload has a text field
    # Assuming the text is stored in a 'text' or 'page_content' field based on common Langchain/LlamaIndex setups
    
    # Since we can't easily do a full-text search without an index, let's pull some chunks and check
    # Alternatively, if we know the document names, we can filter by that.
    
    try:
        # Paginating through some records to find the text
        records, next_page = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            with_payload=True,
            with_vectors=False
        )
        
        found_chunks = []
        for record in records:
            # Check common payload fields for the text content
            content = record.payload.get('text', '') or record.payload.get('page_content', '') or record.payload.get('content', '')
            
            if "non-compete" in content.lower() or "covenant period" in content.lower():
                found_chunks.append({
                    "id": record.id,
                    "source": record.payload.get("metadata", {}).get("source", "Unknown"),
                    "page": record.payload.get("metadata", {}).get("page", "Unknown"),
                    "content_snippet": content[:200] + "..." if len(content) > 200 else content
                })
        
        if found_chunks:
            print(f"✅ Found {len(found_chunks)} chunks containing 'Non-Compete' related terms:\n")
            for idx, chunk in enumerate(found_chunks):
                print(f"--- Match {idx+1} ---")
                print(f"ID: {chunk['id']}")
                print(f"Source: {chunk['source']} (Page {chunk['page']})")
                print(f"Snippet: {chunk['content_snippet']}\n")
        else:
            print("❌ No 'Non-Compete' chunks found in the database. They might not have been indexed.")
            
    except Exception as e:
        print(f"Error checking Qdrant: {e}")

if __name__ == "__main__":
    check_non_compete()
