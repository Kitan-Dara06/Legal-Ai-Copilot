"""
scripts/run_chunk_experiment.py
───────────────────────────────
Grid experiment: measure RAGAS metrics across chunk-size configurations.

Chunk-size grid:
    parent | child
    -------+------
     2000  |  300
     2000  |  500  ← current baseline
     2000  |  700
     2000  |  900
     3000  |  500
     4000  |  800

Each configuration:
  1. Re-chunks the 4 test PDFs with the target sizes
  2. Creates a TEMPORARY Qdrant collection  (never touches "legal_chunks")
  3. Embeds with Cloudflare BGE-M3
  4. Upserts into the temp collection
  5. Runs the full RAG pipeline (search → draft) for 20 Q&A pairs
  6. Scores with RAGAS  (GPT-4o as judge, Cloudflare for embeddings)
  7. Deletes the temp collection
  8. Appends a result row to the summary CSV

Outputs:
  - results/chunk_exp_p{parent}_c{child}.csv   ← per-config detail
  - results/chunk_experiment_summary.csv        ← comparison table
"""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import List, Tuple

import httpx
import pandas as pd
from datasets import Dataset
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    Modifier,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

# ── RAGAS ─────────────────────────────────────────────────────────────────────
from ragas import evaluate
from ragas.metrics import (
    Faithfulness,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
    ResponseRelevancy,
)
from ragas.run_config import RunConfig

# ── Project imports ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.chunker import HierarchicalChunker
from app.services.parser import extract_from_pdf

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
CHUNK_GRID: List[Tuple[int, int]] = [
    (2000, 300),
    (3000, 500),
    (4000, 800),
]

# PDFs to index — same 4 used in the baseline evaluation
TEST_PDFS = [
    "Exhibit 10.pdf",
    "sec.gov_Archives_edgar_data_819793_000089109218004221_e78842ex10u.htm.pdf",
    "sec.gov_Archives_edgar_data_1654672_000149315218000875_ex10-8.htm.pdf",
    "Form of Employment Agreement.pdf",
]

SYNTHETIC_DATA_PATH = "synthetic_eval_data.json"
RESULTS_DIR = Path("results")
ORG_ID = "chunk_experiment"

# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare Embeddings
# ─────────────────────────────────────────────────────────────────────────────
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "").strip()
CF_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1"
CF_EMBED_MODEL = "@cf/baai/bge-m3"
EMBED_DIM = 1024


def _cf_embed(texts: List[str]) -> List[List[float]]:
    """Calls Cloudflare Workers AI /embeddings — batches of up to 100 texts."""
    headers = {
        "Authorization": f"Bearer {CF_API_KEY}",
        "Content-Type": "application/json",
    }
    all_vectors = []
    # Cloudflare allows up to 100 inputs per request
    batch_size = 25
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                f"{CF_BASE_URL}/embeddings",
                headers=headers,
                json={"model": CF_EMBED_MODEL, "input": batch},
            )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Cloudflare embedding failed {r.status_code}: {r.text[:300]}"
            )
        if r.status_code == 429:  # Rate limit
            time.sleep(10)
            raise RuntimeError("Cloudflare Rate Limited")
        data = r.json().get("data", [])
        if not data:
            raise RuntimeError("Cloudflare returned empty embedding data")
        all_vectors.extend([item["embedding"] for item in data])
    return all_vectors


class CloudflareEmbeddingAdapter:
    """RAGAS-compatible embedding adapter backed by Cloudflare Workers AI."""

    def embed_query(self, text: str) -> List[float]:
        try:
            return _cf_embed([text])[0]
        except Exception as e:
            print(f"   ⚠️  CF embed_query failed: {e} — returning zero vector")
            return _cf_embed([text])[0]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        try:
            return _cf_embed(texts)
        except Exception as e:
            print(f"   ⚠️  CF embed_documents failed: {e} — returning zero vectors")

    async def aembed_query(self, text: str) -> List[float]:
        return self.embed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return self.embed_documents(texts)


# ─────────────────────────────────────────────────────────────────────────────
# Sparse Vector (BM25 TF — must match index-time computation)
# ─────────────────────────────────────────────────────────────────────────────
def _sparse_vector(text: str) -> SparseVector:
    import re

    tokens = re.findall(r"\w+", text.lower())
    freq = {}
    for t in tokens:
        idx = int(hashlib.md5(t.encode()).hexdigest(), 16) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1
    return SparseVector(
        indices=list(freq.keys()), values=[float(v) for v in freq.values()]
    )


# ─────────────────────────────────────────────────────────────────────────────
# Qdrant helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_qdrant() -> QdrantClient:
    return QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY"),
        timeout=60.0,
    )


def _create_temp_collection(client: QdrantClient, name: str):
    existing = [c.name for c in client.get_collections().collections]
    if name in existing:
        print(f"   🗑  Deleting leftover collection '{name}'...")
        client.delete_collection(name)

    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        sparse_vectors_config={
            "text-sparse": SparseVectorParams(modifier=Modifier.IDF)
        },
    )
    print(f"   ✅ Created temp collection '{name}'")


def _delete_temp_collection(client: QdrantClient, name: str):
    try:
        client.delete_collection(name)
        print(f"   🗑  Deleted temp collection '{name}'")
    except Exception as e:
        print(f"   ⚠️  Could not delete '{name}': {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Chunk + Embed + Upsert
# ─────────────────────────────────────────────────────────────────────────────
def build_index(
    client: QdrantClient,
    collection_name: str,
    parent_size: int,
    child_size: int,
):
    """Chunks all test PDFs with the given sizes and upserts into collection_name."""
    chunker = HierarchicalChunker(chunk_size=child_size, overlap=child_size // 10)
    # Override parent size by monkey-patching the sliding-window parent chunker size
    # HierarchicalChunker uses 2000 for parent internally; we override here
    from app.services.chunker import RecursiveChunker

    original_sw = chunker._create_sliding_window_hierarchy

    def patched_sw(text, page_num):
        from app.services.chunker import RecursiveChunker

        results = []
        parent_chunker = RecursiveChunker(
            chunk_size=parent_size, overlap=parent_size // 10
        )
        parent_texts = parent_chunker.split_text(text)
        for parent_text in parent_texts:
            pid = str(uuid.uuid4())
            child_chunker = RecursiveChunker(
                chunk_size=child_size, overlap=child_size // 10
            )
            for child_text in child_chunker.split_text(parent_text):
                results.append(
                    {
                        "parent_id": pid,
                        "section_text": parent_text[:parent_size],
                        "chunk_text": child_text,
                        "page_number": page_num,
                        "source_type": "sliding_window",
                    }
                )
        return results

    chunker._create_sliding_window_hierarchy = patched_sw

    # Also patch structured-header path: context window = parent_size
    original_hierarchical = chunker.chunk_hierarchically

    def patched_hierarchical(pages_data):
        import re

        results = []
        for page_obj in pages_data:
            page_num = page_obj["page"]
            text = page_obj.get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            sections = chunker.split_into_sections(text)
            if len(sections) > 1:
                for section in sections:
                    pid = str(uuid.uuid4())
                    context_window = section[:parent_size]
                    for chunk in chunker.base_chunker.split_text(section):
                        results.append(
                            {
                                "parent_id": pid,
                                "section_text": context_window,
                                "chunk_text": chunk,
                                "page_number": page_num,
                                "source_type": "structured_header",
                            }
                        )
            else:
                results.extend(patched_sw(text, page_num))
        return results

    chunker.chunk_hierarchically = patched_hierarchical

    all_chunks = []
    for pdf_path in TEST_PDFS:
        if not os.path.exists(pdf_path):
            print(f"   ⚠️  PDF not found: {pdf_path} — skipping")
            continue
        with open(pdf_path, "rb") as f:
            pages = extract_from_pdf(f)
        chunks = chunker.chunk_hierarchically(pages)
        # Tag with filename
        for c in chunks:
            c["filename"] = os.path.basename(pdf_path)
        all_chunks.extend(chunks)
        print(f"      {os.path.basename(pdf_path)}: {len(chunks)} chunks")

    print(f"   Total chunks: {len(all_chunks)}")

    # Embed in batches of 50
    BATCH = 50
    points = []
    for i in range(0, len(all_chunks), BATCH):
        batch = all_chunks[i : i + BATCH]
        texts = [c["chunk_text"] for c in batch]
        vectors = _cf_embed(texts)
        for j, (chunk, vec) in enumerate(zip(batch, vectors)):
            global_idx = i + j
            search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
            points.append(
                PointStruct(
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_OID, f"{collection_name}_{global_idx}"
                        )
                    ),
                    vector={"": vec, "text-sparse": _sparse_vector(search_text)},
                    payload={
                        "chunk_text": chunk["chunk_text"],
                        "section_text": chunk.get("section_text", ""),
                        "parent_id": chunk.get("parent_id", ""),
                        "page_number": chunk.get("page_number", 0),
                        "filename": chunk["filename"],
                        "org_id": ORG_ID,
                        "source_type": chunk.get("source_type", ""),
                    },
                )
            )
        pct = min(100, int(((i + BATCH) / len(all_chunks)) * 100))
        print(
            f"      Embedded {min(i + BATCH, len(all_chunks))}/{len(all_chunks)} chunks ({pct}%)"
        )

    # Upsert in batches of 100
    for i in range(0, len(points), 100):
        client.upsert(collection_name=collection_name, points=points[i : i + 100])

    print(f"   ✅ Indexed {len(points)} points into '{collection_name}'")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Search (hybrid RRF against temp collection)
# ─────────────────────────────────────────────────────────────────────────────
def search_temp(
    client: QdrantClient,
    collection_name: str,
    query: str,
    top_k: int = 5,
) -> List[str]:
    """Hybrid search against a temp collection. Returns formatted chunk strings."""
    query_vec = _cf_embed([query])[0]
    sparse_q = _sparse_vector(query)

    hits = client.query_points(
        collection_name=collection_name,
        prefetch=[
            Prefetch(query=query_vec, limit=top_k * 3),
            Prefetch(query=sparse_q, using="text-sparse", limit=top_k * 3),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
    ).points

    results = []
    for hit in hits:
        p = hit.payload
        display = p.get("section_text") or p.get("chunk_text", "")
        results.append(
            f"[Source: {p.get('filename', 'Unknown')}, Page: {p.get('page_number', '?')}]\nContent: {display}"
        )
    return results


from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Draft answer with Qwen (Groq) to match production
# ─────────────────────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
def draft_answer(groq_client: Groq, question: str, chunks: List[str]) -> str:
    context = "\n\n".join(chunks)
    if len(context) > 14000:
        context = context[:14000] + "\n\n[...truncated...]"

    try:
        response = groq_client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Senior Legal Analyst. Answer the user's question "
                        "using ONLY the provided context. Be extremely concise; "
                        "provide only the necessary facts. Every claim must cite "
                        "(filename.pdf, Page X). If the information is not in the context, "
                        "say so explicitly."
                    ),
                },
                {
                    "role": "user",
                    "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}",
                },
            ],
            temperature=0,
            max_tokens=600,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"   ❌ Generation failed (will retry): {e}")
        raise e  # Let tenacity handle the retry


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — RAGAS scoring
# ─────────────────────────────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=10, max=60))
def score_with_ragas(
    questions: List[str],
    answers: List[str],
    contexts: List[List[str]],
    references: List[str],
    evaluator_llm,
    evaluator_embeddings,
) -> dict:
    dataset = Dataset.from_dict(
        {
            "user_input": questions,
            "response": answers,
            "retrieved_contexts": contexts,
            "reference": references,
        }
    )

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
    df = result.to_pandas()
    score_cols = [
        c
        for c in df.columns
        if c not in ("user_input", "response", "retrieved_contexts", "reference")
    ]
    return df[score_cols].mean().to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    RESULTS_DIR.mkdir(exist_ok=True)

    # ── Pre-flight checks ────────────────────────────────────────────────────
    print("🔍 Pre-flight checks...")

    if not CF_ACCOUNT_ID or not CF_API_KEY:
        print("❌ CLOUDFLARE_ACCOUNT_ID or CLOUDFLARE_API_KEY not set in .env")
        return

    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print("❌ OPENAI_API_KEY not set in .env")
        return

    print("   Testing Cloudflare embedding...")
    try:
        test_vec = _cf_embed(["pre-flight test"])
        print(f"   ✅ Cloudflare OK — dim={len(test_vec[0])}")
    except Exception as e:
        print(f"   ❌ Cloudflare failed: {e}")
        return

    # ── Load Q&A pairs ───────────────────────────────────────────────────────
    print(f"\n📖 Loading '{SYNTHETIC_DATA_PATH}'...")
    with open(SYNTHETIC_DATA_PATH) as f:
        raw = json.load(f)

    questions, references = [], []
    for item in raw:
        questions.append(item[0])
        references.append(item[1])

    print(f"   Loaded {len(questions)} Q&A pairs.")

    # ── Clients ─────────────────────────────────────────────────────────────
    qdrant_client = _get_qdrant()
    MODAL_ENDPOINT = os.getenv("MODAL_ENDPOINT", "")
    if not MODAL_ENDPOINT:
        print(
            "❌ MODAL_ENDPOINT not set. Please deploy modal_evaluator.py and add the URL to .env"
        )
        return

    print(f"   Connecting to Modal DeepSeek Judge at {MODAL_ENDPOINT}...")

    # Reverting to standard ChatOpenAI (no JSON mode) because Ragas native prompts
    # are built for markdown extraction, and raw JSON mode with long outputs
    # was causing truncation and validation errors in Pydantic.
    raw_evaluator_llm = ChatOpenAI(
        model="Qwen/Qwen2.5-72B-Instruct-AWQ",  # Must match the MODEL_NAME in modal
        api_key="dummy_key_not_needed",
        base_url=f"{MODAL_ENDPOINT}/v1",
        max_tokens=2048,
        temperature=0,
    )
    from ragas.llms import LangchainLLMWrapper

    evaluator_llm = LangchainLLMWrapper(raw_evaluator_llm)

    evaluator_embeddings = CloudflareEmbeddingAdapter()

    groq_api_key = os.getenv("GROQ_API_KEY", "")
    if not groq_api_key:
        print("❌ GROQ_API_KEY not set in .env")
        return
    groq_client = Groq(api_key=groq_api_key)

    # ── Grid loop ────────────────────────────────────────────────────────────
    summary_rows = []

    for parent_size, child_size in CHUNK_GRID:
        collection_name = f"exp_p{parent_size}_c{child_size}"
        print(f"\n{'=' * 65}")
        print(f"🧪 EXPERIMENT: parent={parent_size}, child={child_size}")
        print(f"   Collection: {collection_name}")
        print("=" * 65)

        t_start = time.time()

        try:
            # ── 1. Create temp collection ────────────────────────────────────
            _create_temp_collection(qdrant_client, collection_name)

            # ── 2. Chunk + Embed + Upsert ────────────────────────────────────
            print(f"\n📄 Indexing PDFs (parent={parent_size}, child={child_size})...")
            build_index(qdrant_client, collection_name, parent_size, child_size)

            # ── 3. Generate answers ──────────────────────────────────────────
            print(
                f"\n🤖 Generating answers with GPT-4o ({len(questions)} questions)..."
            )
            answers, contexts = [], []

            for i, q in enumerate(questions):
                print(f"   [{i + 1}/{len(questions)}] {q[:70]}...")
                chunks = search_temp(qdrant_client, collection_name, q, top_k=5)
                try:
                    answer = draft_answer(groq_client, q, chunks)
                except Exception as e:
                    print(f"   🚨 Draft completely failed after retries: {e}")
                    answer = (
                        f"Failed to generate answer due to consecutive API errors: {e}"
                    )

                answers.append(answer)
                contexts.append(chunks)
                # Save intermediate in case of crash
                pd.DataFrame(
                    {
                        "user_input": questions[: len(answers)],
                        "response": answers,
                        "retrieved_contexts": [str(c) for c in contexts],
                        "reference": references[: len(answers)],
                    }
                ).to_csv(
                    RESULTS_DIR
                    / f"chunk_exp_p{parent_size}_c{child_size}_intermediate.csv",
                    index=False,
                )

            # ── 4. RAGAS scoring ─────────────────────────────────────────────
            print(f"\n📊 Scoring with RAGAS...")
            scores = score_with_ragas(
                questions,
                answers,
                contexts,
                references,
                evaluator_llm,
                evaluator_embeddings,
            )

            elapsed = round(time.time() - t_start, 1)
            print(
                f"\n✅ Config (parent={parent_size}, child={child_size}) done in {elapsed}s"
            )
            print(
                f"   context_precision : {scores.get('llm_context_precision_with_reference', 'N/A'):.3f}"
            )
            print(f"   context_recall    : {scores.get('context_recall', 'N/A'):.3f}")
            print(f"   faithfulness      : {scores.get('faithfulness', 'N/A'):.3f}")
            print(f"   answer_relevancy  : {scores.get('answer_relevancy', 'N/A'):.3f}")

            # Save per-config CSV
            per_config_df = pd.DataFrame(
                {
                    "user_input": questions,
                    "response": answers,
                    "retrieved_contexts": [str(c) for c in contexts],
                    "reference": references,
                    **{k: [v] * len(questions) for k, v in scores.items()},
                }
            )
            per_config_df.to_csv(
                RESULTS_DIR / f"chunk_exp_p{parent_size}_c{child_size}.csv",
                index=False,
            )

            summary_rows.append(
                {
                    "parent_size": parent_size,
                    "child_size": child_size,
                    **scores,
                    "elapsed_s": elapsed,
                }
            )

        except KeyboardInterrupt:
            print("\n⛔ Interrupted — saving partial summary...")
            break
        except Exception as e:
            import traceback

            print(f"\n❌ Config (parent={parent_size}, child={child_size}) FAILED: {e}")
            traceback.print_exc()
            summary_rows.append(
                {
                    "parent_size": parent_size,
                    "child_size": child_size,
                    "error": str(e),
                }
            )
        finally:
            # Always delete the temp collection
            _delete_temp_collection(qdrant_client, collection_name)

    # ── Final summary ────────────────────────────────────────────────────────
    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        summary_path = RESULTS_DIR / "chunk_experiment_summary.csv"
        summary_df.to_csv(summary_path, index=False)

        print(f"\n{'=' * 65}")
        print("🏆 CHUNK EXPERIMENT SUMMARY")
        print("=" * 65)

        display_cols = [
            "parent_size",
            "child_size",
            "llm_context_precision_with_reference",
            "context_recall",
            "faithfulness",
            "answer_relevancy",
        ]
        available = [c for c in display_cols if c in summary_df.columns]
        print(summary_df[available].to_string(index=False))
        print(f"\n📁 Full results saved to: {summary_path}")


if __name__ == "__main__":
    main()
