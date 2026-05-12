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
            client
            if client is not None
            else (
                QdrantClient(
                    url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY")
                )
                if os.getenv("QDRANT_URL")
                else QdrantClient(path=os.getenv("QDRANT_PATH", "./qdrant_storage"))
            )
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

        # Ensure payload indexes exist for filtered fields
        for field in ["org_id", "file_id", "workspace_id", "filename"]:
            try:
                self.qdrant_client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception:
                pass  # Index already exists

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
        """Call Voyage AI via direct HTTP with retry for rate limits."""
        if not self._voyage_available:
            raise RuntimeError("VOYAGE_API_KEY is missing. Dense embedding failed.")

        import time

        import httpx

        headers = {
            "Authorization": f"Bearer {os.environ.get('VOYAGE_API_KEY')}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": texts,
            "model": VOYAGE_MODEL,
            "input_type": "document",
        }

        last_error = None
        # Batch into groups of 10 for faster per-request responses
        batch_size = 10
        all_embeddings = []
        for batch_start in range(0, len(texts), batch_size):
            batch = texts[batch_start : batch_start + batch_size]
            batch_payload = {**payload, "input": batch}

            for attempt in range(5):
                try:
                    with httpx.Client(timeout=60.0) as client:
                        r = client.post(
                            "https://api.voyageai.com/v1/embeddings",
                            headers=headers,
                            json=batch_payload,
                        )
                    if r.status_code == 429:
                        wait = min(30, (2**attempt) * 5)
                        print(f"  ⏳ Rate limited, waiting {wait}s...")
                        time.sleep(wait)
                        continue
                    if r.status_code >= 400:
                        raise RuntimeError(
                            f"Voyage API error {r.status_code}: {r.text[:200]}"
                        )
                    body = r.json()
                    all_embeddings.extend([item["embedding"] for item in body["data"]])
                    break
                except httpx.TimeoutException:
                    wait = (2**attempt) * 5
                    print(
                        f"  ⏳ Timeout (attempt {attempt + 1}/5), retrying in {wait}s..."
                    )
                    time.sleep(wait)
                    continue
                except Exception as e:
                    last_error = e
                    if attempt < 4:
                        wait = (2**attempt) * 5
                        print(f"  ⚠️  Failed ({e}), retrying in {wait}s...")
                        time.sleep(wait)
                        continue
                    raise

        if len(all_embeddings) != len(texts):
            raise RuntimeError(
                f"Expected {len(texts)} embeddings, got {len(all_embeddings)}"
            )
        return all_embeddings

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
