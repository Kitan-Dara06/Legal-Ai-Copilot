import os
from dotenv import load_dotenv
from typing import Dict, List, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny, Prefetch, FusionQuery, Fusion

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Global Qdrant Client (replaces ChromaDB)
# ─────────────────────────────────────────────────────────────────────────────
_qdrant_client = None

def get_global_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            api_key=os.getenv("QDRANT_API_KEY"),
        )
    return _qdrant_client

def compute_sparse_vector(text: str):
    """Creates a basic Term Frequency sparse vector for Qdrant (which applies IDF)"""
    import re
    from qdrant_client.models import SparseVector
    tokens = re.findall(r'\w+', text.lower())
    freq = {}
    for t in tokens:
        idx = abs(hash(t)) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1
    
    indices = []
    values = []
    for k, v in freq.items():
        indices.append(k)
        values.append(float(v))
    return SparseVector(indices=indices, values=values)


def _deduplicate_by_parent(results: List[Dict], max_context_chars: int = 12000) -> List[Dict]:
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
                    "is_parent": False
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
                    "is_parent": True
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


def search_hybrid(query_text: str, query_vector: list[float], top_k: int = 5, specific_contract: str = None) -> List[Dict]:
    """
    Global Hybrid Search (All files, or filtered by specific_contract).
    Replaces the legacy ChromaDB engine.search_hybrid.
    """
    qdrant = get_global_qdrant()
    
    search_filter = None
    if specific_contract:
        search_filter = Filter(
            must=[FieldCondition(key="filename", match=MatchValue(value=specific_contract))]
        )

    sparse_vec = compute_sparse_vector(query_text)

    hits = qdrant.query_points(
        collection_name="legal_chunks",
        prefetch=[
            Prefetch(
                query=query_vector,
                filter=search_filter,
                limit=top_k * 3,
            ),
            Prefetch(
                query=sparse_vec,
                using="text-sparse",
                filter=search_filter,
                limit=top_k * 3,
            )
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k * 3,
        with_payload=True,
    ).points

    if not hits:
        return []

    raw_results = []
    for hit in hits:
        p = hit.payload
        display_text = p.get("section_text") or p.get("chunk_text", "")
        formatted_text = f"[Source: {p.get('filename', 'Unknown')}, Page: {p.get('page_number', '?')}]\nContent: {display_text}"
        
        raw_results.append({
            "id": hit.id,
            "text": formatted_text,
            "score": hit.score,
            "metadata": {
                "source":     p.get("filename", ""),
                "page":       p.get("page_number", 0),
                "parent_id":  p.get("parent_id", ""),
                "section_context": p.get("section_text", ""),
                "file_id":    p.get("file_id"),
                "org_id":     p.get("org_id"),
            }
        })

    deduplicated = _deduplicate_by_parent(raw_results, max_context_chars=12000)

    final_output = []
    for doc in deduplicated[:top_k]:
        final_output.append({
            "text": doc["text"],
            "score": doc["score"],
            "metadata": doc["metadata"],
        })

    return final_output


def get_all_contract_names():
    # Helper to get all stored contract filenames, this is less efficient without an explicit facet request or tracking table,
    # but mimics legacy functionality.
    qdrant = get_global_qdrant()
    records, _ = qdrant.scroll(
        collection_name="legal_chunks",
        limit=10000,
        with_payload=["filename"],
        with_vectors=False
    )
    return list(set(r.payload.get("filename") for r in records if r.payload and "filename" in r.payload))

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

def compute_sparse_vector(text: str):
    """Creates a basic Term Frequency sparse vector for Qdrant (which applies IDF)"""
    import re
    from qdrant_client.models import SparseVector
    tokens = re.findall(r'\w+', text.lower())
    freq = {}
    for t in tokens:
        idx = abs(hash(t)) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1
    
    indices = []
    values = []
    for k, v in freq.items():
        indices.append(k)
        values.append(float(v))
    return SparseVector(indices=indices, values=values)

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
            FieldCondition(key="org_id",  match=MatchValue(value=org_id)),
        ]
    )

    sparse_vec = compute_sparse_vector(query_text)

    # ── Native Hybrid Search via Qdrant Prefetch API ──────────────────────────
    hits = qdrant.query_points(
        collection_name="legal_chunks",
        prefetch=[
            Prefetch(
                query=query_vector,
                filter=search_filter,
                limit=top_k * 3,
            ),
            Prefetch(
                query=sparse_vec,
                using="text-sparse",
                filter=search_filter,
                limit=top_k * 3,
            )
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k * 3,
        with_payload=True,
    ).points

    if not hits:
        return []

    # ── Format into structural payload (mimicking the Chroma Output) ──────────
    raw_results = []
    for hit in hits:
        p = hit.payload
        display_text = p.get("section_text") or p.get("chunk_text", "")
        formatted_text = f"[Source: {p.get('filename', 'Unknown')}, Page: {p.get('page_number', '?')}]\nContent: {display_text}"
        
        raw_results.append({
            "id": hit.id,
            "text": formatted_text,
            "score": hit.score,
            "metadata": {
                "source":     p.get("filename", ""),
                "page":       p.get("page_number", 0),
                "parent_id":  p.get("parent_id", ""),
                "section_context": p.get("section_text", ""),
                "file_id":    p.get("file_id"),
                "org_id":     p.get("org_id"),
            }
        })

    # ── Apply Token Budget / Hierarchical Deduplication ───────────────────────
    deduplicated = _deduplicate_by_parent(raw_results, max_context_chars=12000)

    final_output = []
    for doc in deduplicated[:top_k]:
        final_output.append({
            "text": doc["text"],
            "score": doc["score"],
            "metadata": doc["metadata"],
        })

    return final_output

