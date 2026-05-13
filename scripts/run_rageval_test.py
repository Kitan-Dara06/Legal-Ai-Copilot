import os
import sys
import json
import asyncio
import re
import logging
from dotenv import load_dotenv
from groq import Groq

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.planner import execute_plan

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

async def get_rag_answer(question, file_id):
    # Match the schema expected by execute_plan in app/services/planner.py
    plan = {
        "query_type": "search",
        "tools": [
            {"name": "search_tool", "params": {"top_k": 5}},
            {"name": "draft_tool", "params": {"output_format": "prose", "use_cot": True}}
        ]
    }
    
    # execute_plan(plan, question, mode, file_ids, *, org_id)
    try:
        result_dict = await execute_plan(
            plan=plan, 
            question=question, 
            mode="hybrid", 
            file_ids=[file_id], 
            org_id="legal200"
        )
        ans = result_dict.get("final_output", "")
        if not ans:
            logger.warning(f"RAG returned empty output for question: {question[:50]}")
        return str(ans)
    except Exception as e:
        logger.error(f"RAG Pipeline Error for '{question[:50]}': {e}")
        return f"Error: {e}"

def extract_claims(text):
    if not text or len(text) < 10:
        return []
        
    prompt = f"""
    Extract a list of atomic factual claims from the following legal text.
    Each claim should be a single, standalone sentence identifying a specific obligation, right, or fact.
    
    TEXT:
    {text}
    
    OUTPUT FORMAT (JSON):
    {{"claims": ["claim 1", "claim 2", ...]}}
    """
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content).get("claims", [])
    except Exception as e:
        logger.error(f"Claim Extraction Error: {e}")
        return []

def verify_claim_support(claim, source_text):
    if not claim or not source_text:
        return "UNRELATED"
        
    prompt = f"""
    Is the following legal CLAIM supported by the SOURCE TEXT?
    
    CLAIM: "{claim}"
    
    SOURCE TEXT:
    ---
    {source_text}
    ---
    
    Respond with ONLY:
    - "SUPPORTED" if the source contains the fact or value.
    - "CONTRADICTED" if the source says something different or opposite.
    - "NEW_INFO" if the source is silent on this specific factual detail.
    """
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=10
        )
        return response.choices[0].message.content.strip().upper()
    except Exception as e:
        logger.error(f"Claim Verification Error: {e}")
        return "ERROR"

async def evaluate_case(case, file_id):
    question = case['question']
    gt_answer = case['ground_truth']
    contract_file = case['contract_file']
    gt_claims = [c['claim'] for c in case['claims']]
    
    logger.info(f"Processing: {question[:60]}...")
    
    # 1. Get RAG Answer
    rag_answer = await get_rag_answer(question, file_id)
    if "Error:" in rag_answer or not rag_answer:
        return None

    # 2. Extract RAG Claims
    rag_claims = extract_claims(rag_answer)
    if not rag_claims:
        logger.warning(f"No claims extracted from RAG answer for: {question[:50]}")
        # We still want to measure completeness to GT even if RAG failed to make claims
        completeness = 0.0
    else:
        # Load Contract Source for Verification
        with open(os.path.join("rageval_data", contract_file), "r") as f:
            source_text = f.read()

        # --- Completeness ---
        supported_gt_count = 0
        for gt_c in gt_claims:
            status = verify_claim_support(gt_c, rag_answer)
            if "SUPPORTED" in status:
                supported_gt_count += 1
        
        completeness = (supported_gt_count / len(gt_claims)) if gt_claims else 1.0

        # --- Hallucination & Irrelevance ---
        hallucination_count = 0
        irrelevance_count = 0
        
        for rag_c in rag_claims:
            status = verify_claim_support(rag_c, source_text)
            if "CONTRADICTED" in status:
                hallucination_count += 1
            elif "NEW_INFO" in status:
                irrelevance_count += 1
                
        hallucination_rate = (hallucination_count / len(rag_claims))
        irrelevance_rate = (irrelevance_count / len(rag_claims))
        
        return {
            "question": question,
            "completeness": completeness,
            "hallucination": hallucination_rate,
            "irrelevance": irrelevance_rate,
            "num_rag_claims": len(rag_claims)
        }
    return None

async def main():
    if not os.path.exists("rageval_test_set.json") or not os.path.exists("rageval_file_mapping.json"):
        logger.error("Missing synthetic data files.")
        return

    with open("rageval_test_set.json", "r") as f:
        test_set = json.load(f)
    with open("rageval_file_mapping.json", "r") as f:
        file_mapping = json.load(f)

    results = []
    
    print("\nStarting RAGEval Scorer (30 cases)...\n")
    
    for case in test_set:
        file_id = file_mapping.get(case['contract_file'])
        if not file_id: continue
        
        eval_res = await evaluate_case(case, file_id)
        if eval_res:
            results.append(eval_res)
            print(f"   [RESULT] C: {eval_res['completeness']:.2f} | H: {eval_res['hallucination']:.2f} | I: {eval_res['irrelevance']:.2f} | Claims: {eval_res['num_rag_claims']}")
        else:
            print(f"   [SKIPPED] Question failed or returned no claims.")

    if not results:
        print("\n❌ All evaluation cases failed. Check logs.")
        return

    # Aggregates
    avg_c = sum(r['completeness'] for r in results) / len(results)
    avg_h = sum(r['hallucination'] for r in results) / len(results)
    avg_i = sum(r['irrelevance'] for r in results) / len(results)

    print("\n" + "="*50)
    print("RAGEVAL FINAL METRICS")
    print(f"Cases Evaluated: {len(results)}")
    print(f"Completeness Score (Recall):       {avg_c:.4f}")
    print(f"Hallucination Rate (Faithfulness): {avg_h:.4f}")
    print(f"Irrelevance Rate (Parametric):     {avg_i:.4f}")
    print("="*50)
    
    # Save results to CSV for analysis
    import pandas as pd
    pd.DataFrame(results).to_csv("rageval_results.csv", index=False)

if __name__ == "__main__":
    asyncio.run(main())
