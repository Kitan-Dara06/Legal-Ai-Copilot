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
from app.services.chunker import LongUnitChunker
from app.services.embedder import get_embedding

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = "longrag_eval_temp"

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# LLM CONSTANTS
JUDGE_MODEL = "llama-3.1-8b-instant"
GENERATOR_MODEL = "llama-3.1-8b-instant"

def compute_sparse_vector(text: str) -> SparseVector:
    tokens = re.findall(r'\w+', text.lower())
    freq = {}
    for t in tokens:
        idx = int(hashlib.md5(t.encode('utf-8')).hexdigest(), 16) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1
    return SparseVector(indices=list(freq.keys()), values=[float(v) for v in freq.values()])

def multi_tenant_search(client, query_text: str, top_k: int = 2):
    # Retrieve fewer chunks but they are massive (top 2 = ~24,000 chars)
    query_vector = get_embedding([query_text])[0]
    sparse_vec = compute_sparse_vector(query_text)
    
    hits = client.query_points(
        collection_name=QDRANT_COLLECTION,
        prefetch=[
            Prefetch(query=query_vector, using="dense", limit=top_k * 2),
            Prefetch(query=sparse_vec, using="text-sparse", limit=top_k * 2),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
    ).points
    
    contexts = [h.payload.get("chunk_text", "") for h in hits if h.payload]
    return "\n---\n".join(contexts)

def check_recall(question: str, context: str) -> int:
    prompt = f"Can the following question be answered using SOLELY the provided context?\nRespond ONLY with '1' for Yes, or '0' for No.\n\nQ: {question}\n\nContext:\n{context}"
    try:
        res = groq_client.chat.completions.create(model=JUDGE_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=2)
        return 1 if "1" in res.choices[0].message.content.strip() else 0
    except Exception as e: 
        print(f"Recall Error: {e}")
        return 0

def generate_answer(question: str, context: str):
    prompt = f"Answer the user's question based strictly on the provided Context. Do not include outside knowledge.\n\nContext:\n{context}\n\nQuestion: {question}"
    start_time = time.time()
    try:
        res = groq_client.chat.completions.create(model=GENERATOR_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=200)
        answer = res.choices[0].message.content.strip()
        latency = time.time() - start_time
        return answer, latency
    except Exception as e: 
        print(f"Gen Error: {e}")
        return "Error", 0

def check_faithfulness(question: str, context: str, answer: str) -> int:
    prompt = f"""Evaluate the FAITHFULNESS of the Generation to the provided Context.
Does the Generation hallucinate or state facts that are explicitly NOT in the Context?
Respond ONLY with '1' (Faithful - everything is strictly in the Context) or '0' (Unfaithful - contains external facts or hallucinations).

Question: {question}

Context:
{context}

Generation:
{answer}
"""
    try:
        res = groq_client.chat.completions.create(model=JUDGE_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=2)
        return 1 if "1" in res.choices[0].message.content.strip() else 0
    except Exception as e: 
        print(f"Faithfulness Error: {e}")
        return 0

def main():
    print("🚀 Initializing LongRAG Unit Test")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    
    print(f"🧹 Creating temporary collection: {QDRANT_COLLECTION}")
    try:
        client.delete_collection(QDRANT_COLLECTION)
    except Exception: pass
    
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config={"dense": VectorParams(size=1024, distance=Distance.COSINE)},
        sparse_vectors_config={"text-sparse": SparseVectorParams(modifier=Modifier.IDF)}
    )

    pdf_files = glob("pdfs/*.pdf")
    chunker = LongUnitChunker()
    all_chunks = []

    print("\n📦 Chunking PDFs into Massive Units...")
    for file_path in pdf_files:
        with open(file_path, "rb") as f: pages = extract_from_pdf(f)
        if not pages: continue
        chunks = chunker.chunk_hierarchically(pages)
        all_chunks.extend(chunks)
        print(f"  -> {file_path}: {len(chunks)} LongRAG chunks generated.")

    print(f"\n🚀 Embedding and Upserting {len(all_chunks)} massive chunks...")
    BATCH_SIZE = 5
    for batch_start in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[batch_start: batch_start + BATCH_SIZE]
        texts = [c["chunk_text"] for c in batch]
        vectors = get_embedding(texts)
        batch_points = []
        for j, (chunk, vector) in enumerate(zip(batch, vectors)):
            point_id = str(uuid.uuid4())
            sparse_vec = compute_sparse_vector(chunk["chunk_text"])
            batch_points.append(PointStruct(
                id=point_id, vector={"dense": vector, "text-sparse": sparse_vec},
                payload={"chunk_text": chunk["chunk_text"]}
            ))
        client.upsert(QDRANT_COLLECTION, points=batch_points)

    print("\n🧠 Evaluating 15 queries against LongRAG Architecture...")
    with open("parametric_eval_data.json", "r") as f: questions = json.load(f)

    results = []
    for i, q_obj in enumerate(questions[:15]):
        q = q_obj["question"]
        print(f"[{i+1}/50] Q: {q[:60]}...")
        
        # 1. Retrieve
        ctx = multi_tenant_search(client, q, top_k=2) # Top 2 of 3K tokens each = 6K tokens context
        
        # 2. Check Recall
        recall = check_recall(q, ctx)
        
        # 3. Generate Answer & Track Latency
        answer, latency = generate_answer(q, ctx)
        
        # 4. Check Faithfulness
        faithfulness = check_faithfulness(q, ctx, answer) if answer != "Error" else 0
        
        results.append({
            "Question": q,
            "Context_Recall": recall,
            "Generation_Latency_sec": latency,
            "Faithfulness": faithfulness
        })
        time.sleep(1) # Prevent rate limits

    df = pd.DataFrame(results)
    
    # Let's compare to Experiment 6 baseline numbers for printout.
    # Ex 6: Recall = 82% (Clause-Bound)
    # Average Latency and Faithfulness we will assume 0.05 (from RAGEval Exp 7)
    
    print("\n" + "="*50)
    print("LONGRAG EVALUATION RESULTS")
    print(f"LongRAG Context Recall: {df['Context_Recall'].mean():.2%}")
    print(f"LongRAG Faithfulness:   {df['Faithfulness'].mean():.2%}")
    print(f"LongRAG Avg Latency:    {df['Generation_Latency_sec'].mean():.2f} sec")
    
    avg_len = sum(len(c["chunk_text"]) for c in all_chunks) / len(all_chunks)
    print(f"Average Chunk Size:     {avg_len:.0f} chars (~{avg_len/4:.0f} tokens)")
    print("="*50)
    
    df.to_csv("results/longrag_test_results.csv", index=False)

    print("\n🧹 STRICT REQUIRED DELETION: Wiping temporary Qdrant collection...")
    client.delete_collection(QDRANT_COLLECTION)
    print("✅ Collection deleted.")

if __name__ == "__main__":
    main()
