import os
import sys
import json
import uuid
import re
import hashlib
import time
from glob import glob
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, SparseVectorParams, SparseVector, Modifier, Filter, FieldCondition, MatchValue, Prefetch, FusionQuery, Fusion
from groq import Groq
import pandas as pd

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.parser import extract_from_pdf
from app.services.chunker import chunk_text, HierarchicalChunker, LLMClauseBoundaryChunker
from app.services.embedder import get_embedding

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = "chunking_eval_temp"

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

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

def evaluate_context_recall(question: str, context: str) -> int:
    """
    RAGAS Context Recall implementation via LLM.
    Returns 1 if the context contains the answer, 0 if not.
    """
    prompt = f"""Given the following question and context, can the question be answered based SOLELY on the information provided in the context?
Respond with ONLY "1" if Yes, or "0" if No.

Question: {question}

Context:
{context}
"""
    try:
        res = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=2
        )
        answer = res.choices[0].message.content.strip()
        return 1 if "1" in answer else 0
    except Exception as e:
        print(f"Error evaluating recall: {e}")
        return 0

def multi_tenant_search(client, query_text: str, org_id: str, top_k: int = 5):
    query_vector = get_embedding([query_text])[0]
    sparse_vec = compute_sparse_vector(query_text)
    
    search_filter = Filter(
        must=[FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    )
    
    hits = client.query_points(
        collection_name=QDRANT_COLLECTION,
        prefetch=[
            Prefetch(query=query_vector, using="dense", filter=search_filter, limit=top_k * 3),
            Prefetch(query=sparse_vec, using="text-sparse", filter=search_filter, limit=top_k * 3),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
    ).points
    
    contexts = [h.payload.get("section_text", "") + "\n" + h.payload.get("chunk_text", "") for h in hits if h.payload]
    return "\n---\n".join(contexts)

def main():
    if not QDRANT_URL:
        print("❌ QDRANT_URL not set")
        return

    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    
    print(f"🧹 Creating temporary collection: {QDRANT_COLLECTION}")
    client.recreate_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config={"dense": VectorParams(size=1024, distance=Distance.COSINE)},
        sparse_vectors_config={"text-sparse": SparseVectorParams(modifier=Modifier.IDF)}
    )
    client.create_payload_index(QDRANT_COLLECTION, "org_id", "keyword")

    pdf_files = glob("pdfs/*.pdf")
    print(f"Found {len(pdf_files)} PDFs to process.")

    hierarchical_chunker = HierarchicalChunker(chunk_size=1000, overlap=100)
    llm_chunker = LLMClauseBoundaryChunker()

    all_hierarchical_chunks = []
    all_clause_chunks = []

    print("\n📦 Chunking PDFs...")
    for file_path in pdf_files:
        with open(file_path, "rb") as f:
            pages = extract_from_pdf(f)
        
        if not pages: continue
        
        # 1. Hierarchical
        h_chunks = hierarchical_chunker.chunk_hierarchically(pages)
        all_hierarchical_chunks.extend(h_chunks)
        
        # 2. LLM Clause Boundary
        c_chunks = llm_chunker.chunk_hierarchically(pages)
        all_clause_chunks.extend(c_chunks)
        print(f"  Processed {file_path}: {len(h_chunks)} H-Chunks, {len(c_chunks)} C-Chunks")

    def upsert_chunks(chunks, org_id):
        print(f"\n🚀 Upserting {len(chunks)} chunks for {org_id}...")
        BATCH_SIZE = 15
        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]
            texts = [c["chunk_text"] for c in batch]
            try:
                vectors = get_embedding(texts)
            except Exception as e:
                print(f"Embedding failed: {e}")
                continue
            
            batch_points = []
            for j, (chunk, vector) in enumerate(zip(batch, vectors)):
                search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
                sparse_vec = compute_sparse_vector(search_text)
                point_id = str(uuid.uuid4())
                batch_points.append(PointStruct(
                    id=point_id,
                    vector={"dense": vector, "text-sparse": sparse_vec},
                    payload={
                        "org_id": org_id,
                        "chunk_text": chunk["chunk_text"],
                        "section_text": chunk.get("section_text", ""),
                    }
                ))
            client.upsert(QDRANT_COLLECTION, points=batch_points)
    
    upsert_chunks(all_hierarchical_chunks, "chunk_hierarchical")
    upsert_chunks(all_clause_chunks, "chunk_clause")

    # Load queries
    with open("parametric_eval_data.json", "r") as f:
        questions = json.load(f)
    print(f"\n🧠 Evaluating {len(questions)} queries...")

    results = []
    for q_obj in questions:
        q = q_obj["question"]
        print(f"Q: {q}")
        
        ctx_h = multi_tenant_search(client, q, "chunk_hierarchical")
        recall_h = evaluate_context_recall(q, ctx_h)
        
        ctx_c = multi_tenant_search(client, q, "chunk_clause")
        recall_c = evaluate_context_recall(q, ctx_c)
        
        results.append({
            "Question": q,
            "Hierarchical_Recall": recall_h,
            "Clause_Recall": recall_c
        })
    
    df = pd.DataFrame(results)
    print("\n" + "="*50)
    print("CHUNKING COMPARISON RESULTS")
    print(f"Hierarchical Recall:  {df['Hierarchical_Recall'].mean():.2%}")
    print(f"Clause-Bound Recall:  {df['Clause_Recall'].mean():.2%}")
    print("="*50)
    
    avg_len_h = sum(len(c["chunk_text"]) for c in all_hierarchical_chunks) / len(all_hierarchical_chunks) if all_hierarchical_chunks else 0
    avg_len_c = sum(len(c["chunk_text"]) for c in all_clause_chunks) / len(all_clause_chunks) if all_clause_chunks else 0
    
    print(f"Avg Chunk Length (Hierarchical): {avg_len_h:.0f} chars")
    print(f"Avg Chunk Length (Clause):       {avg_len_c:.0f} chars")
    print("="*50)
    
    df.to_csv("results/chunking_eval_results.csv", index=False)

    print("\n🧹 STRICT REQUIRED DELETION: Wiping temporary Qdrant collection...")
    client.delete_collection(QDRANT_COLLECTION)
    print("✅ Collection deleted.")

if __name__ == "__main__":
    main()
