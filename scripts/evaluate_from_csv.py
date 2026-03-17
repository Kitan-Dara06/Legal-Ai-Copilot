import ast
import os
import time

import httpx
import pandas as pd
from datasets import Dataset
from dotenv import load_dotenv
from openai import OpenAI
from openai import OpenAI as OpenAIWrapper

# ── Ragas Core ──
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

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Workers AI Embedding Client
# ─────────────────────────────────────────────────────────────────────────────
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "").strip()
CF_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1"
CF_EMBED_MODEL = "@cf/baai/bge-m3"

def _cf_embed(texts: list[str]) -> list[list[float]]:
    headers = {"Authorization": f"Bearer {CF_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": CF_EMBED_MODEL, "input": texts}
    with httpx.Client(timeout=60.0) as client:
        r = client.post(f"{CF_BASE_URL}/embeddings", headers=headers, json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"Cloudflare embedding failed {r.status_code}: {r.text[:300]}")
    return [item["embedding"] for item in r.json().get("data", [])]

class CloudflareEmbeddingAdapter:
    def embed_query(self, text: str):
        try: return _cf_embed([text])[0]
        except Exception: return [0.0] * 1024
    def embed_documents(self, texts: list[str]):
        try: return _cf_embed(texts)
        except Exception: return [[0.0] * 1024 for _ in texts]
    async def aembed_query(self, text: str): return self.embed_query(text)
    async def aembed_documents(self, texts: list[str]): return self.embed_documents(texts)

# ─────────────────────────────────────────────────────────────────────────────
# Main Evaluation
# ─────────────────────────────────────────────────────────────────────────────
def run_evaluation_from_csv():
    print("1. Loading Data from ragas_intermediate.csv...")
    if not os.path.exists("ragas_intermediate.csv"):
        print("Error: ragas_intermediate.csv not found!")
        return

    df = pd.read_csv("ragas_intermediate.csv")
    df["retrieved_contexts"] = df["retrieved_contexts"].apply(ast.literal_eval)
    dataset = Dataset.from_pandas(df)

    print("\n2. Initializing Clients...")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    # ── Judge LLM: GPT-4o (The gold standard) ────────────────────────────────
    openai_wrapper = OpenAIWrapper(api_key=openai_key)
    evaluator_llm = llm_factory("gpt-4o", client=openai_wrapper)

    # ── Embeddings: Cloudflare ───────────────────────────────────────────────
    evaluator_embeddings = CloudflareEmbeddingAdapter()

    print("\n3. Starting Ragas Evaluation (Judge: GPT-4o)...")
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

    result_df = result.to_pandas()
    result_df.to_csv("ragas_baseline.csv", index=False)
    print("\n✅ Results saved to ragas_baseline.csv")
    
    score_cols = [c for c in result_df.columns if c not in ("user_input", "response", "retrieved_contexts", "reference")]
    print("\n🏆 FINAL REPORT CARD 🏆")
    print("-" * 25)
    print(result_df[score_cols].mean().to_string())

if __name__ == "__main__":
    run_evaluation_from_csv()
