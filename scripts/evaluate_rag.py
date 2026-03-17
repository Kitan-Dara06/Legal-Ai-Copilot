import json
import os
import time

import httpx
import pandas as pd
from datasets import Dataset
from dotenv import load_dotenv
from groq import Groq  # For the generator
from openai import OpenAI  # For OpenRouter embeddings
from openai import OpenAI as OpenAIWrapper  # To trick Ragas into using Groq

from ragas import evaluate
from ragas.embeddings import OpenAIEmbeddings as RagasOpenAIEmbeddings
from ragas.llms import llm_factory
from ragas.metrics import (
    Faithfulness,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
    ResponseRelevancy,
)
from ragas.run_config import RunConfig

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.planner import create_execution_plan, execute_plan

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Workers AI Embedding Client
# ─────────────────────────────────────────────────────────────────────────────
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "").strip()
CF_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1"
CF_EMBED_MODEL = "@cf/baai/bge-m3"


def _cf_embed(texts: list) -> list:
    headers = {
        "Authorization": f"Bearer {CF_API_KEY}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{CF_BASE_URL}/embeddings",
            headers=headers,
            json={"model": CF_EMBED_MODEL, "input": texts},
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Cloudflare embedding failed {r.status_code}: {r.text[:300]}")
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError("Cloudflare returned empty embedding data")
    return [item["embedding"] for item in data]


class CloudflareEmbeddingAdapter:
    """RAGAS-compatible embedding adapter backed by Cloudflare Workers AI."""

    def embed_query(self, text: str) -> list:
        try:
            return _cf_embed([text])[0]
        except Exception as e:
            print(f"   ⚠️  CF embed_query failed: {e} — returning zero vector")
            return [0.0] * 1024

    def embed_documents(self, texts: list) -> list:
        try:
            return _cf_embed(texts)
        except Exception as e:
            print(f"   ⚠️  CF embed_documents failed: {e} — returning zero vectors")
            return [[0.0] * 1024 for _ in texts]

    async def aembed_query(self, text: str) -> list:
        return self.embed_query(text)

    async def aembed_documents(self, texts: list) -> list:
        return self.embed_documents(texts)


def run_evaluation():
    print("1. Initializing Direct Groq & OpenRouter Clients...")

    openai_api_key = os.getenv("OPENAI_API_KEY")

    # Wrapper for Ragas Judge (OpenAI GPT-4o)
    openai_wrapper = OpenAIWrapper(api_key=openai_api_key)

    # ── 2. The Judge (GPT-4o) ──
    evaluator_llm = llm_factory("gpt-4o", client=openai_wrapper)

    # ── Embeddings: Cloudflare Workers AI ────────────────────────────────────
    evaluator_embeddings = CloudflareEmbeddingAdapter()
    print("   Testing Cloudflare embedding endpoint...")
    try:
        test_vec = _cf_embed(["Legal contract test."])
        print(f"   ✅ Cloudflare OK — dim={len(test_vec[0])}")
    except Exception as e:
        print(f"   ❌ Cloudflare embedding test FAILED: {e}")
        return

    # ── Configuration (Updated to match your actual Qdrant payload) ──
    TEST_ORG_ID = "stream_ui_org"
    TEST_FILE_IDS = [
        121, 122,123,124
    ]  # Specific file IDs to simulate an exact session
    TARGET_FILES = [
        "Exhibit 10.pdf",
        "sec.gov_Archives_edgar_data_819793_000089109218004221_e78842ex10u.htm.pdf",
        "sec.gov_Archives_edgar_data_1654672_000149315218000875_ex10-8.htm.pdf",
        "Form of Employment Agreement.pdf",
    ]
    # ── 3. Load Synthetic Data ──
    try:
        with open("synthetic_eval_data.json", "r") as f:
            synthetic_data = json.load(f)
            test_questions = []
            ground_truths = []
            i = 0
            while i < len(synthetic_data):
                item = synthetic_data[i]
                if (
                    item[0] == "Q"
                    and i + 1 < len(synthetic_data)
                    and synthetic_data[i + 1][0] == "A"
                ):
                    test_questions.append(item[1])
                    ground_truths.append([synthetic_data[i + 1][1]])
                    i += 2
                else:
                    test_questions.append(item[0])
                    ground_truths.append([item[1]])
                    i += 1
    except FileNotFoundError:
        return print("Error: synthetic_eval_data.json missing.")

    print("2. Running RAG Pipeline (using Pydantic AI for generation)...")
    answers = []
    contexts_list = []

    for i, q in enumerate(test_questions):
        print(f"   -> Processing: {q[:50]}...")
        import time

        time.sleep(10)  # Flat 10s cooldown to avoid TPM rate limits

        # Save intermediate results frequently in case of failure
        temp_data = {
            "user_input": test_questions[: len(answers)],
            "response": answers,
            "retrieved_contexts": contexts_list,
            "reference": [gt[0] for gt in ground_truths[: len(answers)]],
        }
        pd.DataFrame(temp_data).to_csv("ragas_intermediate.csv", index=False)

        # Step A & B: Full Agent Execution (Search + Read + Logic + Draft)
        try:
            import tenacity
            
            @tenacity.retry(stop=tenacity.stop_after_attempt(3), wait=tenacity.wait_exponential(multiplier=1, min=4, max=15))
            def get_result():
                plan = create_execution_plan(q)
                return execute_plan(
                    plan, q, mode="hybrid", file_ids=TEST_FILE_IDS, org_id=TEST_ORG_ID
                )
                
            result = get_result()
            chunks = [str(c) for c in result.get("context", {}).get("chunks", [])]
            final_ans = str(result.get("final_output", ""))
            
            # Token limit safeguard for Ragas evaluation phase
            if len(final_ans) > 4000:
                final_ans = final_ans[:4000] + "... [Truncated]"
                
            contexts_list.append(chunks)
            answers.append(final_ans)
            
        except Exception as e:
            print(f"   ❌ Generation failed for query: {e}")
            contexts_list.append([])
            answers.append(f"Failed due to generation error: {e}")

    # ── 4. Format & Evaluate ──
    data = {
        "user_input": test_questions,
        "response": answers,
        "retrieved_contexts": contexts_list,
        "reference": [gt[0] for gt in ground_truths],
    }
    dataset = Dataset.from_dict(data)

    # Save intermediate results so we skip the 15-minute generation phase if Ragas crashes
    pd.DataFrame(data).to_csv("ragas_intermediate.csv", index=False)

    print("\n3. Starting Ragas Evaluation (Sequential Mode)...")
    result = evaluate(
        dataset=dataset,
        metrics=[
            LLMContextPrecisionWithReference(llm=evaluator_llm),
            LLMContextRecall(llm=evaluator_llm),
            Faithfulness(llm=evaluator_llm),
            ResponseRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings),
        ],
        run_config=RunConfig(max_workers=1),
    )

    print("\n✅ Results saved to ragas_baseline.csv")
    result.to_pandas().to_csv("ragas_baseline.csv", index=False)


if __name__ == "__main__":
    run_evaluation()
