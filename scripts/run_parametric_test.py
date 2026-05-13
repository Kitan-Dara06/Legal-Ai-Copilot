import json
import os
import asyncio
import pandas as pd
from numpy import dot
from numpy.linalg import norm
from dotenv import load_dotenv

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.planner import create_execution_plan, execute_plan
import cohere

load_dotenv()
load_dotenv(".env")

# Setup Cohere
cohere_api_key = os.getenv("COHERE_API_KEY")
if not cohere_api_key:
    raise ValueError("COHERE_API_KEY is not set.")
co_client = cohere.Client(cohere_api_key)

def calculate_cosine_similarity(text_a: str, text_b: str) -> float:
    """Calculates Cosine Similarity between two texts using Cohere Embeddings."""
    if not text_a.strip() or not text_b.strip():
        return 0.0
    
    # We use search_document for long texts.
    response = co_client.embed(
        texts=[text_a, text_b],
        model="embed-english-v3.0",
        input_type="search_document"
    )
    embeddings = response.embeddings
    vec_a = embeddings[0]
    vec_b = embeddings[1]
    
    return dot(vec_a, vec_b) / (norm(vec_a) * norm(vec_b))

async def run_parametric_evaluation():
    TEST_ORG_ID = "legal200"
    TEST_FILE_IDS = [100, 101, 102, 103] 
    
    try:
        with open("parametric_eval_data.json", "r") as f:
            data = json.load(f)
            test_questions = [item["question"] for item in data]
    except FileNotFoundError:
        print("Error: parametric_eval_data.json missing. Please generate it first.")
        return

    print(f"Loaded {len(test_questions)} questions. Starting Parametric vs Contextual Test...")
    
    results = []
    
    for i, q in enumerate(test_questions):
        print(f"\n--- Question {i+1}/{len(test_questions)} ---")
        print(f"Q: {q}")
        
        # ── Pipeline A: Full RAG ──
        try:
            print("[Running RAG Pipeline...]")
            plan_rag = await create_execution_plan(q)
            result_rag = await execute_plan(plan_rag, q, mode="hybrid", file_ids=TEST_FILE_IDS, org_id=TEST_ORG_ID)
            ans_rag = str(result_rag.get("final_output", "")).strip()
        except Exception as e:
            ans_rag = f"Error: {e}"
            print(f"RAG Error: {e}")
            
        # ── Pipeline B: Baseline (No-RAG) ──
        try:
            print("[Running Baseline / No-RAG...]")
            # Skip retrieval by passing a customized plan sequence with only the draft tool
            plan_no_rag = {
                "query_type": "search",
                "tools": [{"name": "draft_tool", "params": {"output_format": "prose"}}],
                "reasoning": "Baseline Parametric Evaluation"
            }
            result_no_rag = await execute_plan(plan_no_rag, q, mode="hybrid", file_ids=TEST_FILE_IDS, org_id=TEST_ORG_ID)
            ans_no_rag = str(result_no_rag.get("final_output", "")).strip()
        except Exception as e:
            ans_no_rag = f"Error: {e}"
            print(f"No-RAG Error: {e}")

        # Compute Similarity
        similarity = 0.0
        if not ans_rag.startswith("Error") and not ans_no_rag.startswith("Error"):
            try:
                similarity = calculate_cosine_similarity(ans_rag, ans_no_rag)
            except Exception as e:
                print(f"Embedding error: {e}")
        
        print(f"Similarity: {similarity:.4f}")
        
        results.append({
            "question": q,
            "rag_answer": ans_rag,
            "norag_answer": ans_no_rag,
            "similarity": similarity
        })
        
        # Save intermediate
        pd.DataFrame(results).to_csv("parametric_test_results.csv", index=False)
        
    # Final Analysis
    df = pd.DataFrame(results)
    avg_sim = df["similarity"].mean()
    
    print("\n" + "="*50)
    print(f"TEST COMPLETE! Evaluated {len(test_questions)} queries.")
    print(f"Average Cosine Similarity (RAG vs No-RAG): {avg_sim:.4f}")
    
    if avg_sim >= 0.85:
        print("\n⚠️ CONCLUSION: PARAMETRIC OVERRIDE DETECTED ⚠️")
        print("Your LLM's answers barely change with or without retrieved documents.")
        print("It is likely echoing its training data boundaries instead of relying on the RAG context.")
    elif avg_sim <= 0.60:
        print("\n✅ CONCLUSION: HIGH CONTEXT DEPENDENCE ✅")
        print("Your LLM changes its answers significantly when provided with retrieved documents.")
        print("This confirms the 90% faithfulness metric is grounded in actual contextual usage!")
    else:
        print("\n⚖️ CONCLUSION: MIXED DEPENDENCE ⚖️")
        print("The system moderately shifts based on context. Further prompts tuning may enforce stronger grounding.")
        
    print("="*50)

if __name__ == "__main__":
    asyncio.run(run_parametric_evaluation())
