import os
import sys
import uuid
import json
import re
import hashlib
from typing import List
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, SparseVectorParams, SparseVector, Modifier

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.parser import extract_from_pdf
from app.services.chunker import chunk_text
from app.services.embedder import get_embedding
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = "legal_chunks"
ORG_ID = "stream_ui_org"

# Matching TARGET_FILES and TEST_FILE_IDS from evaluate_rag.py
FILES_TO_INGEST = [
    {"name": "Exhibit 10.pdf", "id": 104},
    {"name": "sec.gov_Archives_edgar_data_819793_000089109218004221_e78842ex10u.htm.pdf", "id": 106},
    {"name": "sec.gov_Archives_edgar_data_1654672_000149315218000875_ex10-8.htm.pdf", "id": 107},
    {"name": "Form of Employment Agreement.pdf", "id": 109},
]

def compute_sparse_vector(text: str) -> SparseVector:
    """Stable TF sparse vector builder."""
    tokens = re.findall(r'\w+', text.lower())
    freq = {}
    for t in tokens:
        hex_digest = hashlib.md5(t.encode('utf-8')).hexdigest()
        idx = int(hex_digest, 16) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1
    
    indices = []
    values = []
    for k, v in freq.items():
        indices.append(k)
        values.append(float(v))
    return SparseVector(indices=indices, values=values)

def main():
    if not QDRANT_URL:
        print("❌ QDRANT_URL not set in .env")
        return

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    
    # Ensure collection exists and is fresh
    existing = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION in existing:
        print(f"🗑️ Wiping existing collection: {QDRANT_COLLECTION}")
        client.delete_collection(QDRANT_COLLECTION)
    
    print(f"🏗️ Creating collection: {QDRANT_COLLECTION}")
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        sparse_vectors_config={"text-sparse": SparseVectorParams(modifier=Modifier.IDF)}
    )
    
    # Indexes for performance
    client.create_payload_index(QDRANT_COLLECTION, "file_id", "integer")
    client.create_payload_index(QDRANT_COLLECTION, "org_id", "keyword")
    client.create_payload_index(QDRANT_COLLECTION, "filename", "keyword")

    for file_info in FILES_TO_INGEST:
        filename = file_info["name"]
        file_id = file_info["id"]
        file_path = os.path.join(os.getcwd(), filename)
        
        if not os.path.exists(file_path):
            print(f"⚠️ Skipping {filename}: File not found at {file_path}")
            continue

        print(f"\n📄 Processing: {filename} (ID: {file_id})")
        
        with open(file_path, "rb") as f:
            pages_data = extract_from_pdf(f)
        
        if not pages_data:
            print(f"   ❌ Extraction failed for {filename}")
            continue
            
        chunks = chunk_text(pages_data)
        print(f"   -> {len(chunks)} chunks created.")

        BATCH_SIZE = 5
        points_count = 0
        @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=30))
        def upsert_with_retry(points):
            client.upsert(QDRANT_COLLECTION, points=points)

        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]
            texts = [c["chunk_text"] for c in batch]
            vectors = get_embedding(texts)
            
            if not vectors:
                print(f"   ⚠️ Embedding failed for batch {batch_start}")
                continue

            batch_points = []
            for i, (chunk, vector) in enumerate(zip(batch, vectors)):
                global_idx = batch_start + i
                search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
                sparse_vec = compute_sparse_vector(search_text)
                
                point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{ORG_ID}_{file_id}_{global_idx}"))
                
                batch_points.append(PointStruct(
                    id=point_id,
                    vector={"": vector, "text-sparse": sparse_vec},
                    payload={
                        "file_id": file_id,
                        "org_id": ORG_ID,
                        "chunk_text": chunk["chunk_text"],
                        "section_text": chunk.get("section_text", ""),
                        "parent_id": chunk.get("parent_id", ""),
                        "source_type": chunk.get("source_type", ""),
                        "page_number": chunk.get("page_number", 0),
                        "filename": filename,
                    }
                ))
            upsert_with_retry(batch_points)
            points_count += len(batch_points)
        
        print(f"   ✅ Upserted {points_count} points.")

    print("\n🎉 Headless ingestion complete!")

if __name__ == "__main__":
    main()
