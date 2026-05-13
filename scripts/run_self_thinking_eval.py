import os
import sys
import json
import asyncio
import time
import pandas as pd
from typing import List, Tuple
from dotenv import load_dotenv
from groq import AsyncGroq, Groq

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.legal_primitives import search_tool, _draft_simple, _draft_with_cot

load_dotenv()

JUDGE_MODEL = "llama-3.3-70b-versatile"
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def judge_faithfulness(question: str, context: str, answer: str) -> int:
    """Scores whether generated answer contains factual hallucinations compared to the context."""
    prompt = f"""Evaluate the FAITHFULNESS of the Generation to the provided Context.
Does the Generation hallucinate or state facts that are explicitly NOT in the Context?
Respond ONLY with '1' (Faithful - everything is strictly in the Context) or '0' (Unfaithful - contains external facts or hallucinations).

Question: {question}
Context: {context}
Generation: {answer}
"""
    try:
        res = client.chat.completions.create(
            model=JUDGE_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=2
        )
        return 1 if "1" in res.choices[0].message.content.strip() else 0
    except Exception as e:
        print(f"Faithfulness Judge Error: {e}")
        return 0

def judge_equivalence(ans_a: str, ans_b: str, question: str) -> int:
    """Uses BERTScore equivalent via LLM semantic equivalence check: Do these two answers provide the same legal advice?"""
    prompt = f"""You are a senior lawyer grading two associate memos.
We do not care about length, formatting, or exact phrasing. We solely care about semantic equivalence.
Do Response A and Response B offer exactly the SAME LEGAL ADVICE and come to the identical factual conclusion regarding the scenario?

Question: {question}

Response A (Implicit CoT):
{ans_a}

Response B (Explicit Self-Thinking CoT):
{ans_b}

Respond ONLY with '1' (Identical Legal Advice) or '0' (Different details, advice, or meaning)."""
    try:
        res = client.chat.completions.create(
            model=JUDGE_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=2
        )
        return 1 if "1" in res.choices[0].message.content.strip() else 0
    except Exception as e:
        print(f"Equivalence Judge Error: {e}")
        return 0

async def main():
    print("🚀 Initializing Experiment 4: Self-Thinking Prompt Evaluation")
    
    with open("parametric_eval_data.json", "r") as f:
        questions = json.load(f)[:20] # Take first 20 for evaluation
        
    results = []
    
    for i, q_obj in enumerate(questions):
        question = q_obj["question"]
        print(f"\n[{i+1}/20] Evaluating Q: {question[:50]}...")
        
        # 1. Retrieve Hybrid Context
        try:
            context_chunks = await search_tool(query=question, top_k=4, mode="hybrid", org_id="legal200")
            context_str = "\n".join(context_chunks)
        except Exception as e:
            print(f"Retrieval Error: {e}")
            continue
            
        # 2. Generate Implicit CoT Answer
        t0 = time.time()
        implicit_ans = await _draft_simple(context_chunks, question, "prose")
        implicit_time = time.time() - t0
        implicit_faith = judge_faithfulness(question, context_str, implicit_ans)
        
        # 3. Generate Explicit Context (Self-Thinking via Pydantic AI)
        t0 = time.time()
        explicit_ans = await _draft_with_cot(context_chunks, "prose", question)
        explicit_time = time.time() - t0
        explicit_faith = judge_faithfulness(question, context_str, explicit_ans)
        
        # 4. Measure Legal Equivalence
        equivalence = judge_equivalence(implicit_ans, explicit_ans, question)
        
        results.append({
            "Question": question,
            "Target Concept": q_obj.get("target_concept", ""),
            "Implicit_Answer": implicit_ans,
            "Explicit_Answer": explicit_ans,
            "Implicit_Faithfulness": implicit_faith,
            "Explicit_Faithfulness": explicit_faith,
            "Implicit_Latency_sec": implicit_time,
            "Explicit_Latency_sec": explicit_time,
            "Equivalent_Legal_Advice": equivalence
        })
        time.sleep(1) # Rate limit padding
        
    df = pd.DataFrame(results)
    
    print("\n" + "="*50)
    print("🎯 SELF-THINKING EVALUATION RESULTS (Experiment 4)")
    print(f"Implicit CoT Faithfulness: {df['Implicit_Faithfulness'].mean():.2%}")
    print(f"Explicit CoT Faithfulness: {df['Explicit_Faithfulness'].mean():.2%}")
    print(f"Legal Advice Equivalence:  {df['Equivalent_Legal_Advice'].mean():.2%}")
    print(f"\nAverage Latency (Implicit): {df['Implicit_Latency_sec'].mean():.2f}s")
    print(f"Average Latency (Explicit): {df['Explicit_Latency_sec'].mean():.2f}s")
    print("="*50)
    
    # Save divergence cases for manual review
    divergence = df[df['Equivalent_Legal_Advice'] == 0]
    print(f"\n⚠️ Identified {len(divergence)} cases where Explicit Reasoning changed the legal advice.")
    
    os.makedirs("results", exist_ok=True)
    df.to_csv("results/self_thinking_eval.csv", index=False)
    print("✅ Results saved to results/self_thinking_eval.csv")

if __name__ == "__main__":
    asyncio.run(main())
