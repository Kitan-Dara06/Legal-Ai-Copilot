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
from typing import Dict, List

import voyageai
from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

# Import the existing BGE-M3 fallback logic from legal_rag
from app.services.embedder import get_embedding as get_bge_embeddings

QDRANT_PATH = os.environ.get("QDRANT_PATH", "./qdrant_storage")
COLLECTION_NAME = "lex_unified_chunks"

VOYAGE_DIM = 1024
BGE_DIM = 1024
VOYAGE_MODEL = "voyage-law-2"


class LegalEmbedder:
    def __init__(self, collection_name: str = COLLECTION_NAME, client: QdrantClient = None):
        self.collection_name = collection_name
        self.qdrant_client = client if client is not None else QdrantClient(path=QDRANT_PATH)

        voyage_key = os.environ.get("VOYAGE_API_KEY")
        self._voyage_available = bool(voyage_key)

        if self._voyage_available:
            print(f"Initialising Voyage AI client (model: {VOYAGE_MODEL})...")
            self._voyage = voyageai.Client(api_key=voyage_key)
        else:
            print("⚠️  VOYAGE_API_KEY not set — falling back to BGE-M3 as primary.")

        print("Loading SPLADE sparse model (fastembed)...")
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
                    "dense_voyage": VectorParams(size=VOYAGE_DIM, distance=Distance.COSINE),
                    "dense_bge": VectorParams(size=BGE_DIM, distance=Distance.COSINE),
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
        """Call Voyage AI with fallback to BGE on any error."""
        if not self._voyage_available:
            return self._embed_bge(texts)
        try:
            result = self._voyage.embed(texts, model=VOYAGE_MODEL, input_type="document")
            return result.embeddings
        except Exception as e:
            print(f"  ⚠️ Voyage embed failed ({e}), falling back to BGE.")
            return self._embed_bge(texts)

    def _embed_bge(self, texts: List[str]) -> List[List[float]]:
        return get_bge_embeddings(texts)

    def _embed_splade(self, text: str) -> SparseVector:
        result = list(self._splade.embed([text]))[0]
        return SparseVector(
            indices=result.indices.tolist(),
            values=result.values.tolist(),
        )

    # ------------------------------------------------------------------
    # Document indexing
    # ------------------------------------------------------------------

    def index_document(self, document_name: str, chunks: List[Dict]):
        if not chunks:
            print("No chunks to index.")
            return

        print(f"Indexing {len(chunks)} chunks for '{document_name}'...")

        rich_texts = [self._format_text(c) for c in chunks]
        voyage_vecs = self._embed_voyage(rich_texts)

        # BGE-M3 stored alongside voyage
        bge_vecs = self._embed_bge(rich_texts)

        points = []
        for i, (chunk, rich_text, v_vec, b_vec) in enumerate(
            zip(chunks, rich_texts, voyage_vecs, bge_vecs)
        ):
            splade_vec = self._embed_splade(rich_text)
            point_id = abs(hash(f"{document_name}_{i}")) % (10**15)

            points.append(
                PointStruct(
                    id=point_id,
                    vector={
                        "dense_voyage": v_vec,
                        "dense_bge": b_vec,
                        "sparse_legal": splade_vec,
                    },
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
        print(f"✓ Indexed {len(points)} chunks for '{document_name}' (voyage-law-2 + nomic + SPLADE).")

    # ------------------------------------------------------------------
    # Query vector methods (for HybridRetriever)
    # ------------------------------------------------------------------

    def get_voyage_query_vector(self, text: str) -> List[float]:
        """Embed a query with voyage-law-2 (falls back to bge on failure)."""
        if not self._voyage_available:
            return self.get_bge_query_vector(text)
        try:
            result = self._voyage.embed([text], model=VOYAGE_MODEL, input_type="query")
            return result.embeddings[0]
        except Exception as e:
            print(f"  ⚠️ Voyage query embed failed ({e}), falling back to bge.")
            return self.get_bge_query_vector(text)

    def get_bge_query_vector(self, text: str) -> List[float]:
        """Embed a query with BGE-M3."""
        return get_bge_embeddings([text])[0]

    def get_splade_query_vector(self, text: str) -> SparseVector:
        return self._embed_splade(text)
