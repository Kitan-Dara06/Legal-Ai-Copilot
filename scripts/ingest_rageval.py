import os
import sys
import uuid
import re
import hashlib
import json
from glob import glob
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, SparseVectorParams, SparseVector, Modifier
from tenacity import retry, stop_after_attempt, wait_exponential

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.chunker import chunk_text
from app.services.embedder import get_embedding

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = "lex_unified_chunks"
ORG_ID = "legal200"

def compute_sparse_vector(text: str) -> SparseVector:
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
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    
    # We DO NOT wipe the collection this time because we might want to keep Experiment 1 data
    # but for RAGEval we only search these specific file IDs.
    
    txt_files = glob("rageval_data/*.txt")
    print(f"Found {len(txt_files)} synthetic contracts to ingest.")

    file_mapping = {}

    for i, file_path in enumerate(txt_files):
        filename = os.path.basename(file_path)
        file_id = 200 + i # 200 series for RAGEval
        file_mapping[filename] = file_id

        print(f"\n📄 Processing: {filename} (ID: {file_id})")
        
        with open(file_path, "r") as f:
            text = f.read()
        
        # Format like the parser output
        pages_data = [{"page": 1, "text": text}]
            
        chunks = chunk_text(pages_data)
        print(f"   -> {len(chunks)} chunks created.")

        BATCH_SIZE = 10
        points_count = 0
        
        @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=30))
        def upsert_with_retry(points):
            client.upsert(QDRANT_COLLECTION, points=points)

        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]
            texts = [c["chunk_text"] for c in batch]
            vectors = get_embedding(texts)
            
            if not vectors:
                continue

            batch_points = []
            for j, (chunk, vector) in enumerate(zip(batch, vectors)):
                global_idx = batch_start + j
                search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
                sparse_vec = compute_sparse_vector(search_text)
                
                point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{ORG_ID}_{file_id}_{global_idx}"))
                
                batch_points.append(PointStruct(
                    id=point_id,
                    vector={"dense": vector, "text-sparse": sparse_vec},
                    payload={
                        "file_id": file_id,
                        "org_id": ORG_ID,
                        "chunk_text": chunk["chunk_text"],
                        "section_text": chunk.get("section_text", ""),
                        "page_number": chunk.get("page_number", 1),
                        "filename": filename,
                    }
                ))
            upsert_with_retry(batch_points)
            points_count += len(batch_points)
        
        print(f"   ✅ Upserted {points_count} points.")

    # Save mapping for the runner
    with open("rageval_file_mapping.json", "w") as f:
        json.dump(file_mapping, f)

if __name__ == "__main__":
    main()
