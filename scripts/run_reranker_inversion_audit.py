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

# Hugging Face Router Endpoint (Updated for 2026)
# Standard cross-encoder task via router
HF_API_URL = "https://router.huggingface.co/v1/rerank" # Trying if they have a standard rerank task

def query_hf_rerank(query, docs, model="cross-encoder/ms-marco-MiniLM-L-6-v2"):
    """ HF Router often follows Cohere-style or proprietary rerank APIs now """
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "query": query,
        "documents": docs,
        "top_n": len(docs)
    }
    # If the router doesn't support /rerank, this will 404
    response = requests.post(HF_API_URL, headers=headers, json=payload)
    return response

def query_cohere(query, docs):
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
    
    # 1. Cohere (Baseline)
    co_res = query_cohere(q, docs)
    co_scores = [0.0, 0.0]
    if "results" in co_res:
        for r in co_res["results"]:
            co_scores[r["index"]] = r["relevance_score"]
            
    # 2. HF (MonoT5 / MiniLM)
    # Since router /rerank is new/experimental, let's try a fallback to completions if it fails
    # But for now, let's just log Cohere if HF is 404
    hf_gap = 0.0
    
    return {
        "topic": pair['topic'],
        "co_scores": co_scores,
        "co_gap": co_scores[0] - co_scores[1],
        "hf_gap": hf_gap
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
        print(f"\nEvaluating Topic: {pair['topic']}")
        print(f"   Query: {pair['query']}")
        res = await evaluate_pair(pair)
        results.append(res)
        print(f"   Correct Score:  {res['co_scores'][0]:.6f}")
        print(f"   Inverted Score: {res['co_scores'][1]:.6f}")
        print(f"   📊 GAP:         {res['co_gap']:.6f}")

    # Aggregates
    avg_co_gap = sum(r['co_gap'] for r in results) / len(results)
    
    print("\n" + "="*50)
    print("COHERE RERANK V3 INVERSION AUDIT")
    print(f"Average Legal Semantic Gap: {avg_co_gap:.6f}")
    print("="*50)
    
    if avg_co_gap > 0.1:
        print("\n✅ COHERE: Successfully distinguishing negations.")
    elif avg_co_gap > 0.0:
         print("\n⚠️ COHERE: Extremely weak negation sensitivity.")
    else:
        print("\n❌ COHERE: NEGATION BLIND / SYCOPHANTIC")

if __name__ == "__main__":
    asyncio.run(main())
