"""
Hybrid Retriever — voyage-law-2 / nomic + SPLADE + RRF
========================================================
Updated to use the new vector names (dense_voyage, dense_nomic) from
embedder.py's rebuilt Qdrant collection.

Also adds an optional defined-terms conflict check: after retrieval, each
hit is annotated with conflicted_terms if the registry detects a conflict
for any defined term present in the clause text.
"""

import re
import warnings
from typing import Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Fusion,
    FusionQuery,
    NamedSparseVector,
    NamedVector,
    Prefetch,
    SparseVector,
)

warnings.filterwarnings("ignore", category=UserWarning, module="qdrant_client")

QDRANT_PATH = "./qdrant_storage"

from app.services.ingestion.embedder import COLLECTION_NAME, LegalEmbedder


class HybridRetriever:
    """
    RRF fusion across dense_voyage, dense_nomic, and sparse_legal.
    Accepts pre-computed query vectors from LegalEmbedder.
    """

    def __init__(
        self,
        qdrant_path: str = QDRANT_PATH,
        collection_name: str = COLLECTION_NAME,
        client: QdrantClient = None,
        registry=None,
    ):
        self.client = client if client is not None else QdrantClient(path=qdrant_path)
        self.collection_name = collection_name
        self.registry = registry  # optional RegistryQueryEngine for conflict annotation

    def search(
        self,
        query: str,
        limit: int = 5,
    ) -> List[Dict]:
        """
        Retrieves using both dense_voyage and sparse_legal (SPLADE) with RRF fusion.
        fastembed is lazily loaded when first queried.
        """
        print(f"[HybridRetriever] Hybrid search: '{query[:70]}' (limit={limit})")

        embedder = LegalEmbedder(
            client=self.client, collection_name=self.collection_name
        )
        voyage_vector = embedder.get_voyage_query_vector(query)
        splade_vector = embedder.get_splade_query_vector(query)

        prefetch = [
            Prefetch(
                query=voyage_vector,
                using="dense_voyage",
                limit=limit * 2,
            )
        ]

        if splade_vector:
            prefetch.append(
                Prefetch(
                    query=splade_vector,
                    using="sparse_legal",
                    limit=limit * 2,
                )
            )

        try:
            results = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=prefetch,
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                with_payload=True,
            )

            hits = []
            for hit in results.points:
                payload = hit.payload or {}
                hierarchy = payload.get("hierarchy_path", [])
                node_id = (
                    " > ".join(hierarchy)
                    if hierarchy
                    else payload.get("document_name", "Unknown")
                )
                chunk = {
                    "node_id": node_id,
                    "score": round(hit.score, 4),
                    "text": payload.get("raw_text", ""),
                    "document_name": payload.get("document_name", "Unknown"),
                    "source_document": payload.get("document_name", "Unknown"),
                    "page_number": payload.get("page_number", 0),
                    "clause_reference": payload.get("clause_reference", node_id),
                    "has_conflict": False,
                    "conflicted_terms": [],
                }
                hits.append(chunk)

            # --- Defined-terms conflict annotation ---
            if self.registry and hits:
                self._annotate_conflicts(hits)

            print(f"  ↳ RRF returned {len(hits)} results.")
            return hits

        except Exception as e:
            print(f"⚠️ WARNING: Hybrid search failed. Reason: {e}")
            return []

    def _annotate_conflicts(self, hits: List[Dict]):
        """
        For each retrieved chunk, scan clause text for defined terms
        that have cross-document conflicts in the registry.
        Modifies hits in place.
        """
        try:
            all_conflicts = self.registry.detect_conflicts()
            if not all_conflicts:
                return

            for hit in hits:
                text = hit.get("text", "")
                conflicted = []
                for term in all_conflicts:
                    if re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
                        conflicted.append(term)
                if conflicted:
                    hit["has_conflict"] = True
                    hit["conflicted_terms"] = conflicted
        except Exception as e:
            print(f"  ⚠️  Conflict annotation failed: {e}")
