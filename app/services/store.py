import os
import uuid
from typing import Dict, List, Optional

import cohere
import httpx
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
)
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Global Qdrant Client (replaces ChromaDB)
# ─────────────────────────────────────────────────────────────────────────────
_qdrant_client = None
_cohere_client = None


def get_global_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=os.getenv("QDRANT_API_KEY"),
            timeout=60,
        )
    return _qdrant_client


def get_cohere_client() -> cohere.ClientV2 | None:
    global _cohere_client
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        return None
    if _cohere_client is None:
        _cohere_client = cohere.ClientV2(
            api_key=api_key,
            httpx_client=httpx.Client(timeout=120.0),
        )
    return _cohere_client


from app.utils.vector_utils import compute_sparse_vector as _compute_sparse_dict


def compute_sparse_vector(text: str):
    """
    Creates a Term Frequency sparse vector for Qdrant (which applies IDF at index time).
    Uses the shared vector_utils dictionary builder to ensure index/query consistency.
    """
    from qdrant_client.models import SparseVector

    d = _compute_sparse_dict(text)
    return SparseVector(indices=d["indices"], values=d["values"])


def _deduplicate_by_parent(
    results: List[Dict], max_context_chars: int = 12000
) -> List[Dict]:
    """
    Hierarchical Retrieval for Qdrant hits: Groups child chunks by parent_id.
    Returns parent context (section_context) instead of child chunks.
    """
    parent_map = {}

    for r in results:
        pid = r["metadata"].get("parent_id", "")
        parent_text = r["metadata"].get("section_context", "")
        child_text = r["text"]

        # Fallback: If no parent or parent is empty, treat child as standalone
        if not pid or not parent_text or parent_text.strip() == "":
            unique_key = r.get("id", r["text"])
            if unique_key not in parent_map:
                parent_map[unique_key] = {
                    "text": child_text,
                    "metadata": r["metadata"],
                    "score": r["score"],
                    "matched_children": 1,
                    "is_parent": False,
                }
        else:
            # Group by parent
            if pid not in parent_map:
                formatted_parent = f"[Source: {r['metadata'].get('source', 'Unknown')}, Page: {r['metadata'].get('page', '?')}]\nContent: {parent_text}"
                parent_map[pid] = {
                    "text": formatted_parent,
                    "metadata": r["metadata"],
                    "score": r["score"],
                    "matched_children": 1,
                    "is_parent": True,
                }
            else:
                parent_map[pid]["score"] += r["score"] * 0.5
                parent_map[pid]["matched_children"] += 1

    deduplicated = sorted(parent_map.values(), key=lambda x: x["score"], reverse=True)

    total_chars = 0
    final_results = []
    for doc in deduplicated:
        text_len = len(doc["text"])
        if total_chars + text_len > max_context_chars:
            break
        final_results.append(doc)
        total_chars += text_len

    return final_results


def search_hybrid(
    query_text: str,
    query_vector: list[float],
    top_k: int = 5,
    specific_contract: Optional[str] = None,
    org_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    nomic_vector: Optional[list[float]] = None,
) -> List[Dict]:
    """
    Hybrid Search using dual dense (voyage + nomic) + sparse (SPLADE) with RRF.
    Filtered by org_id (mandatory) + optionally workspace_id + specific_contract.
    """
    qdrant = get_global_qdrant()

    if not org_id:
        raise ValueError("org_id is required for tenant isolation.")
    org_id_str = str(org_id)

    must_conditions: List[FieldCondition] = [
        FieldCondition(key="org_id", match=MatchValue(value=org_id_str))
    ]
    if workspace_id:
        must_conditions.append(
            FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))
        )
    if specific_contract:
        must_conditions.append(
            FieldCondition(key="filename", match=MatchValue(value=specific_contract))
        )

    search_filter = Filter(must=list(must_conditions)) if must_conditions else None

    sparse_vec = compute_sparse_vector(query_text)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def fetch_qdrant_points():
        prefetches = [
            Prefetch(
                query=query_vector,
                using="dense_voyage",
                filter=search_filter,
                limit=top_k * 3,
            ),
            Prefetch(
                query=sparse_vec,
                using="sparse_legal",
                filter=search_filter,
                limit=top_k * 3,
            ),
        ]
        if nomic_vector:
            prefetches.append(
                Prefetch(
                    query=nomic_vector,
                    using="dense_nomic",
                    filter=search_filter,
                    limit=top_k * 3,
                )
            )
        return qdrant.query_points(
            collection_name="legal_chunks",
            prefetch=prefetches,
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k * 3,
            with_payload=True,
        ).points

    hits = fetch_qdrant_points()

    if not hits:
        return []

    raw_results = []
    for hit in hits:
        p = hit.payload or {}
        display_text = p.get("section_text") or p.get("chunk_text", "")
        formatted_text = f"[Source: {p.get('filename', 'Unknown')}, Page: {p.get('page_number', '?')}]\\nContent: {display_text}"

        raw_results.append(
            {
                "id": hit.id,
                "text": formatted_text,
                "score": hit.score,
                "metadata": {
                    "source": p.get("filename", ""),
                    "page": p.get("page_number", 0),
                    "parent_id": p.get("parent_id", ""),
                    "section_context": p.get("section_text", ""),
                    "file_id": p.get("file_id"),
                    "org_id": p.get("org_id"),
                    "clause_reference": p.get("clause_reference", ""),
                },
            }
        )

    deduplicated = _deduplicate_by_parent(raw_results, max_context_chars=12000)

    final_output = []
    for doc in deduplicated[:top_k]:
        final_output.append(
            {
                "text": doc["text"],
                "score": doc["score"],
                "metadata": doc["metadata"],
            }
        )

    return final_output


def get_all_contract_names(org_id: str):
    """
    Returns all stored contract filenames for a given org.
    org_id is required for proper tenant isolation.
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    qdrant = get_global_qdrant()

    if not org_id:
        raise ValueError("org_id is required for tenant isolation.")

    search_filter = Filter(
        must=[FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    )

    records, _ = qdrant.scroll(
        collection_name="legal_chunks",
        limit=10000,
        with_payload=["filename"],
        with_vectors=False,
        scroll_filter=search_filter,
    )
    return list(
        set(
            r.payload.get("filename")
            for r in records
            if r.payload and "filename" in r.payload
        )
    )


def extract_sources_from_chunks(chunks: list[str]):
    import re

    sources = set()
    for chunk in chunks:
        match = re.search(r"\[Source: (.+?),", chunk)
        if match:
            sources.add(match.group(1))
    return list(sources)


# ─────────────────────────────────────────────────────────────────────────────
# Qdrant-Backed Search (Session-Scoped)
# Used when a Redis session is active — searches ONLY the selected files.
# Returns results in the same format as search_hybrid so legal_primitives
# doesn't need structural changes.
# ─────────────────────────────────────────────────────────────────────────────


def search_hybrid_qdrant(
    query_text: str,
    query_vector: list[float],
    file_ids: list[int],
    org_id: str,
    top_k: int = 5,
) -> list[dict]:
    """
    Searches Qdrant filtered by file_ids + org_id (session scope).
    Uses Qdrant's native Hybrid Search (Prefetch + Reciprocal Rank Fusion)
    by querying both the dense vector and the 'text-sparse' BM25 vector.
    """
    qdrant = get_global_qdrant()

    # Build filter: file_id IN [...] AND org_id == "..."
    search_filter = Filter(
        must=[
            FieldCondition(key="file_id", match=MatchAny(any=file_ids)),
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
        ]
    )

    sparse_vec = compute_sparse_vector(query_text)

    # ── Native Hybrid Search via Qdrant Prefetch API ──────────────────────────
    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    def fetch_qdrant_points():
        return qdrant.query_points(
            collection_name="legal_chunks",
            prefetch=[
                Prefetch(
                    query=query_vector,
                    using="dense",
                    filter=search_filter,
                    limit=top_k * 5,
                ),
                Prefetch(
                    query=sparse_vec,
                    using="text-sparse",
                    filter=search_filter,
                    limit=top_k * 5,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k * 5,
            with_payload=True,
        ).points

    hits = fetch_qdrant_points()

    if not hits:
        return []

    # ── Format into structural payload (mimicking the Chroma Output) ──────────
    raw_results = []
    for hit in hits:
        p = hit.payload or {}
        display_text = p.get("section_text") or p.get("chunk_text", "")
        formatted_text = f"[Source: {p.get('filename', 'Unknown')}, Page: {p.get('page_number', '?')}]\nContent: {display_text}"

        raw_results.append(
            {
                "id": hit.id,
                "text": formatted_text,
                "score": hit.score,
                "metadata": {
                    "source": p.get("filename", ""),
                    "page": p.get("page_number", 0),
                    "parent_id": p.get("parent_id", ""),
                    "section_context": p.get("section_text", ""),
                    "file_id": p.get("file_id"),
                    "org_id": p.get("org_id"),
                    "clause_reference": p.get("clause_reference", ""),
                },
            }
        )

    # ── Cohere Reranker V3 (with RRF fallback) ───────────────────────────────
    cohere_client = get_cohere_client()
    documents_for_rerank = [res["text"] for res in raw_results]

    if cohere_client is None:
        logger.warning(
            "COHERE_API_KEY not set — using Qdrant RRF scores (no reranking)"
        )
        reranked_results = sorted(raw_results, key=lambda x: x["score"], reverse=True)
    else:
        try:
            rerank_response = cohere_client.rerank(
                model="rerank-english-v3.0",
                query=query_text,
                documents=documents_for_rerank,
                top_n=len(raw_results),
            )
            reranked_results = []
            for result in rerank_response.results:
                original_doc = raw_results[result.index]
                original_doc["score"] = result.relevance_score
                reranked_results.append(original_doc)
        except Exception as rerank_err:
            logger.error(
                "[COHERE_FAIL] Reranker failed for query '%s': %s — falling back to Qdrant RRF",
                query_text[:80],
                str(rerank_err)[:200],
            )
            reranked_results = sorted(
                raw_results, key=lambda x: x["score"], reverse=True
            )

    # ── ResearchLog: Log retrieval data for evaluation ──────────────────────
    try:
        from app.database import AsyncSessionLocal
        from app.models import ResearchLog

        async def _log_research():
            async with AsyncSessionLocal() as log_db:
                log_entry = ResearchLog(
                    id=uuid.uuid4(),
                    workflow_id=None,
                    goal_id=None,
                    org_id=uuid.UUID(org_id) if isinstance(org_id, str) else org_id,
                    query_text=query_text,
                    retrieved_chunks={
                        "count": len(raw_results),
                        "top_scores": [r.get("score", 0) for r in reranked_results[:5]],
                    },
                    reranker_scores={
                        "model": "rerank-english-v3.0",
                        "top_scores": [r.get("score", 0) for r in reranked_results[:5]],
                    },
                    faithfulness_score=None,
                    negation_sensitivity_score=None,
                )
                log_db.add(log_entry)
                await log_db.commit()

        import asyncio

        asyncio.ensure_future(_log_research())
    except Exception:
        pass  # Research logging is best-effort

    # ── Apply Token Budget / Hierarchical Deduplication ───────────────────────
    deduplicated = _deduplicate_by_parent(reranked_results, max_context_chars=12000)

    final_output = []
    reranker_metrics = {
        "top_1_score": 0.0,
        "score_spread": 0.0,
        "mean_score": 0.0,
        "score_count": 0,
    }
    for doc in deduplicated[:top_k]:
        final_output.append(
            {
                "text": doc["text"],
                "score": doc["score"],
                "metadata": doc["metadata"],
            }
        )

    # Extract reranker metrics for ResearchLog instrumentation
    if final_output:
        scores = [d["score"] for d in final_output]
        reranker_metrics["top_1_score"] = max(scores)
        reranker_metrics["score_spread"] = max(scores) - min(scores)
        reranker_metrics["mean_score"] = sum(scores) / len(scores)
        reranker_metrics["score_count"] = len(scores)

    return {"results": final_output, "reranker_metrics": reranker_metrics}
