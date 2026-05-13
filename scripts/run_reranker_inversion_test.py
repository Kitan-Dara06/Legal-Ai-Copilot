import os
import sys
import json
import asyncio
import requests
import time
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN")
COHERE_API_KEY = os.getenv("COHERE_API_KEY")

# Hugging Face Inference API Config (updated router endpoint)
HF_API_URL = "https://router.huggingface.co/models/cross-encoder/ms-marco-MiniLM-L-6-v2"
HF_HEADERS = {"Authorization": f"Bearer {HF_TOKEN}"}

def query_hf(payload):
    response = requests.post(HF_API_URL, headers=HF_HEADERS, json=payload)
    # Check if model is loading
    if response.status_code == 503:
        print("Model is loading, waiting 10s...")
        time.sleep(10)
        return query_hf(payload)
    try:
        return response.json()
    except:
        print(f"HF ERROR (Non-JSON): {response.status_code} - {response.text}")
        return {"error": "Non-JSON response"}

def query_cohere(query, docs):
    """ Calls Cohere Rerank API directly for comparison """
    url = "https://api.cohere.ai/v1/rerank"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {COHERE_API_KEY}"
    }
    payload = {
        "model": "rerank-english-v3.0",
        "query": query,
        "documents": docs,
        "top_n": len(docs)
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()

async def evaluate_pair(pair):
    q = pair['query']
    docs = [pair['correct'], pair['inverted']]
    
    # 1. Hugging Face Score
    hf_res = query_hf({"inputs": {"source_sentence": q, "sentences": docs}})
    # print(f"DEBUG HF: {hf_res}") # Debugging
    
    hf_scores = [0.0, 0.0]
    if isinstance(hf_res, list):
        # Some HF cross-encoders return a list of scores directly, others return [{'score': ...}, ...]
        for i, item in enumerate(hf_res):
            if i < 2:
                hf_scores[i] = item if isinstance(item, (int, float)) else item.get("score", 0)
    elif isinstance(hf_res, dict) and "error" in hf_res:
         print(f"HF ERROR: {hf_res['error']}")
    
    # 2. Cohere Score
    co_res = query_cohere(q, docs)
    co_scores = [0.0, 0.0]
    if "results" in co_res:
        for r in co_res["results"]:
            co_scores[r["index"]] = r["relevance_score"]
            
    return {
        "topic": pair['topic'],
        "hf_scores": hf_scores,
        "co_scores": co_scores,
        "hf_gap": hf_scores[0] - hf_scores[1],
        "co_gap": co_scores[0] - co_scores[1]
    }

async def main():
    if not os.path.exists("reranker_test_set.json"):
        print("Missing test set.")
        return

    with open("reranker_test_set.json", "r") as f:
        test_set = json.load(f)

    print(f"Starting Reranker Semantic Inversion Test ({len(test_set)} pairs)...")
    results = []
    
    for pair in test_set:
        print(f"Evaluating: {pair['topic']}...")
        res = await evaluate_pair(pair)
        results.append(res)
        print(f"   📊 HF Gap: {res['hf_gap']:.4f}, Cohere Gap: {res['co_gap']:.4f}")

    # Aggregates
    avg_hf_gap = sum(r['hf_gap'] for r in results) / len(results)
    avg_co_gap = sum(r['co_gap'] for r in results) / len(results)

    print("\n" + "="*50)
    print("RERANKER INVERSION FINAL METRICS (Average Gap)")
    print(f"MiniLM-L-6-v2 (HF): {avg_hf_gap:.4f}")
    print(f"Cohere Rerank V3:   {avg_co_gap:.4f}")
    print("="*50)
    
    if avg_co_gap > 0.2:
        print("\n✅ COHERE STATUS: NEGATION SENSITIVE")
    else:
        print("\n⚠️ COHERE STATUS: NEGATION BLIND")

if __name__ == "__main__":
    asyncio.run(main())
