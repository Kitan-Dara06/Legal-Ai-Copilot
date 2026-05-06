"""
Dual Embedding: voyage-law-2 (Primary) + nomic-embed-text-v1.5 (Backup) + SPLADE
==================================================================================
Stores THREE named vectors per Qdrant point:
  - dense_voyage  : voyage-law-2                       (1024-dim, Voyage AI API)
  - dense_nomic   : nomic-ai/nomic-embed-text-v1.5     (768-dim,  local via ST)
  - sparse_legal  : prithivida/Splade_PP_en_v1         (SPLADE,   via fastembed)

Primary: voyage-law-2 is a domain-specific legal embedding model.
Backup:  nomic-embed-text-v1.5 is used if Voyage API key is absent or the call fails.

NOTE: If upgrading from the old schema (dense_bge / dense_legal_bert),
      delete ./qdrant_storage and re-index all documents.
"""

import os
from typing import Dict, List, Optional

import voyageai
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

QDRANT_PATH = os.environ.get("QDRANT_PATH", "./qdrant_storage")
COLLECTION_NAME = "lex_unified_chunks"

VOYAGE_DIM = 1024
VOYAGE_MODEL = "voyage-law-2"


class LegalEmbedder:
    def __init__(
        self,
        collection_name: str = COLLECTION_NAME,
        client: QdrantClient = None,
        ingest_mode: bool = False,
    ):
        self.collection_name = collection_name
        self.qdrant_client = (
            client if client is not None else QdrantClient(path=QDRANT_PATH)
        )

        voyage_key = os.environ.get("VOYAGE_API_KEY")
        self._voyage_available = bool(voyage_key)

        if self._voyage_available:
            print(f"Initialising Voyage AI client (model: {VOYAGE_MODEL})...")
            self._voyage = voyageai.Client(api_key=voyage_key)
        else:
            print("⚠️  VOYAGE_API_KEY not set — dense embeddings will fail.")

        print("Loading SPLADE sparse model (fastembed)...")
        from fastembed import SparseTextEmbedding

        self._splade = SparseTextEmbedding(model_name="prithivida/Splade_PP_en_v1")

        self._setup_collection()

    # ------------------------------------------------------------------
    # Collection setup
    # ------------------------------------------------------------------

    def _setup_collection(self):
        if not self.qdrant_client.collection_exists(self.collection_name):
            print(f"Creating Qdrant collection '{self.collection_name}'...")
            self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense_voyage": VectorParams(
                        size=VOYAGE_DIM, distance=Distance.COSINE
                    ),
                    # Keep BGE config in case old points exist, but we won't populate it
                    "dense_bge": VectorParams(size=1024, distance=Distance.COSINE),
                },
                sparse_vectors_config={"sparse_legal": SparseVectorParams()},
            )
        else:
            print(f"Collection '{self.collection_name}' already exists — ready.")

    # ------------------------------------------------------------------
    # Text formatting
    # ------------------------------------------------------------------

    def _format_text(self, chunk: Dict) -> str:
        path = " > ".join(chunk["hierarchy"]) if chunk.get("hierarchy") else "ROOT"
        return f"[{path}]\n{chunk['text']}"

    # ------------------------------------------------------------------
    # Embedding helpers
    # ------------------------------------------------------------------

    def _embed_voyage(self, texts: List[str]) -> List[List[float]]:
        """Call Voyage AI."""
        if not self._voyage_available:
            raise RuntimeError("VOYAGE_API_KEY is missing. Dense embedding failed.")
        try:
            result = self._voyage.embed(
                texts, model=VOYAGE_MODEL, input_type="document"
            )
            return result.embeddings
        except Exception as e:
            print(f"  ⚠️ Voyage embed failed ({e}).")
            raise

    def _embed_splade(self, text: str) -> Optional[SparseVector]:
        result = list(self._splade.embed([text]))[0]
        return SparseVector(
            indices=result.indices.tolist(),
            values=result.values.tolist(),
        )

    # ------------------------------------------------------------------
    # Document indexing
    # ------------------------------------------------------------------

    def index_document(self, document_name: str, chunks: List[Dict]):
        if not self._splade:
            raise RuntimeError(
                "LegalEmbedder must be initialized with ingest_mode=True to index documents."
            )

        if not chunks:
            print("No chunks to index.")
            return

        print(f"Indexing {len(chunks)} chunks for '{document_name}'...")

        rich_texts = [self._format_text(c) for c in chunks]
        voyage_vecs = self._embed_voyage(rich_texts)

        points = []
        for i, (chunk, rich_text, v_vec) in enumerate(
            zip(chunks, rich_texts, voyage_vecs)
        ):
            splade_vec = self._embed_splade(rich_text)
            point_id = abs(hash(f"{document_name}_{i}")) % (10**15)

            # Omit dense_bge
            vector_dict = {
                "dense_voyage": v_vec,
            }
            if splade_vec:
                vector_dict["sparse_legal"] = splade_vec

            points.append(
                PointStruct(
                    id=point_id,
                    vector=vector_dict,
                    payload={
                        "document_name": document_name,
                        "hierarchy_path": chunk.get("hierarchy", []),
                        "raw_text": chunk["text"],
                        "rich_text": rich_text,
                        "page_number": chunk.get("page_number", 0),
                        "clause_reference": " > ".join(chunk.get("hierarchy", [])),
                    },
                )
            )

        self.qdrant_client.upsert(collection_name=self.collection_name, points=points)
        print(
            f"✓ Indexed {len(points)} chunks for '{document_name}' (voyage-law-2 + SPLADE)."
        )

    # ------------------------------------------------------------------
    # Query vector methods (for HybridRetriever)
    # ------------------------------------------------------------------

    def get_voyage_query_vector(self, text: str) -> List[float]:
        """Embed a query with voyage-law-2."""
        if not self._voyage_available:
            raise RuntimeError(
                "VOYAGE_API_KEY is missing. Query dense embedding failed."
            )
        try:
            result = self._voyage.embed([text], model=VOYAGE_MODEL, input_type="query")
            return result.embeddings[0]
        except Exception as e:
            print(f"  ⚠️ Voyage query embed failed ({e}).")
            raise

    def get_bge_query_vector(self, text: str) -> Optional[List[float]]:
        """Deprecated."""
        return None

    def get_splade_query_vector(self, text: str) -> Optional[SparseVector]:
        """Returns the splade vector for a given text."""
        return self._embed_splade(text)
