import os
import sys
import json
import asyncio
import re
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.legal_primitives import search_tool

load_dotenv()

def extract_chunk_ids(chunks):
    """ Extracts ID: XXX from the [Source: ..., ID: XXX] prefix. """
    ids = []
    for chunk in chunks:
        match = re.search(r"ID: (\w+)", chunk)
        if match:
            ids.append(match.group(1))
    return ids

def calculate_jaccard(list1, list2):
    if not list1 and not list2:
        return 1.0
    s1 = set(list1)
    s2 = set(list2)
    intersection = len(s1.intersection(s2))
    union = len(s1.union(s2))
    return intersection / union if union > 0 else 0.0

async def evaluate_pair(pair):
    correct = pair['correct_premise']
    false = pair['false_premise']
    topic = pair['topic']
    
    # We use the same org_id and file_ids as the other tests
    org_id = "legal200"
    file_ids = [100, 101, 102, 103, 200, 201, 202, 203, 204, 205] # Mix of real and synthetic
    
    # Run search for both
    res_correct = await search_tool(query=correct, org_id=org_id, file_ids=file_ids, top_k=5)
    res_false = await search_tool(query=false, org_id=org_id, file_ids=file_ids, top_k=5)
    
    ids_correct = extract_chunk_ids(res_correct)
    ids_false = extract_chunk_ids(res_false)
    
    jaccard = calculate_jaccard(ids_correct, ids_false)
    top1_stable = (ids_correct[0] == ids_false[0]) if ids_correct and ids_false else False
    
    return {
        "topic": topic,
        "jaccard": jaccard,
        "top1_stable": top1_stable,
        "correct_ids": ids_correct,
        "false_ids": ids_false
    }

async def main():
    if not os.path.exists("sycophancy_test_set.json"):
        print("Missing sycophancy test set.")
        return

    with open("sycophancy_test_set.json", "r") as f:
        test_set = json.load(f)

    print(f"Starting Retrieval Sycophancy Test ({len(test_set)} pairs)...")
    results = []
    
    for pair in test_set:
        print(f"Evaluating: {pair['topic']}...")
        res = await evaluate_pair(pair)
        results.append(res)
        print(f"   📊 Overlap: {res['jaccard']:.2%}, Top-1 Stable: {res['top1_stable']}")

    # Aggregates
    avg_jaccard = sum(r['jaccard'] for r in results) / len(results)
    stability_rate = sum(1 for r in results if r['top1_stable']) / len(results)

    print("\n" + "="*50)
    print("RETRIEVAL SYCOPHANCY FINAL METRICS")
    print(f"Average Chunk Overlap (Jaccard): {avg_jaccard:.2%}")
    print(f"Top-1 Selection Stability:       {stability_rate:.2%}")
    print("="*50)
    
    if avg_jaccard > 0.70:
        print("\n✅ CONCLUSION: ROBUST RETRIEVAL")
        print("The retriever picks the same information regardless of the question's premise.")
    elif avg_jaccard < 0.40:
        print("\n⚠️ CONCLUSION: HIGH SYCOPHANCY DETECTED")
        print("The retriever is being misled into picking different chunks by the false premise.")
    else:
        print("\n⚖️ CONCLUSION: MODERATE SYCOPHANCY")
        print("Some semantic drift detected between neutral and biased queries.")

if __name__ == "__main__":
    asyncio.run(main())
