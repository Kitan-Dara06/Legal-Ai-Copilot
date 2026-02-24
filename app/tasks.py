# app/tasks.py
#
# PURPOSE: The "Paralegal" — background tasks that process uploaded files.
#
# Uses the EXISTING pipeline:
#   parser.py    → extract_from_pdf()   — extracts text per page
#   chunker.py   → chunk_text()         — hierarchical chunking (parent/child)
#   embedder.py  → get_embedding()      — OpenAI text-embedding-3-small
#
# Then upserts to Qdrant (instead of ChromaDB) with file_id + org_id for isolation.
#
# Flow:
#   1. API uploads file → saves to Postgres (PENDING) → triggers this task
#   2. This task: Parse → Chunk → Embed → Upsert to Qdrant
#   3. Updates Postgres status to READY (or FAILED)
#   4. Updates Redis progress every batch so the UI can show "45% done"

import io
import os

from app.worker import celery_app
from dotenv import load_dotenv
from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams, SparseVectorParams, SparseVector, Modifier

# ── Your existing services (unchanged) ───────────────────────────────────────
from app.services.parser import extract_from_pdf
from app.services.chunker import chunk_text
from app.services.embedder import get_embedding

load_dotenv()

QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY")   # Required for Qdrant Cloud
QDRANT_COLLECTION = "legal_chunks"
host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = os.getenv("UPSTASH_PASSWORD")


# ─────────────────────────────────────────────────────────────────────────────
# Qdrant Client (Synchronous — Celery doesn't use async)
# ─────────────────────────────────────────────────────────────────────────────
_qdrant_client = None

def get_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant_client


def ensure_collection_exists(client: QdrantClient):
    """Creates the Qdrant collection if it doesn't already exist."""
    existing = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=1024,          # BGE-M3 output size via OpenRouter
                distance=Distance.COSINE,
            ),
            sparse_vectors_config={
                "text-sparse": SparseVectorParams(
                    modifier=Modifier.IDF
                )
            }
        )
        
        # Create an index for file_id so we can filter by it
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="file_id",
            field_schema="integer"
        )
        # Create an index for org_id so we can filter by it
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="org_id",
            field_schema="keyword"
        )
        # Create an index for filename so read_tool can filter by contract name
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="filename",
            field_schema="keyword"
        )

# ── Sparse Vector builder ────────────────────────────────────────────────────
def compute_sparse_vector(text: str) -> SparseVector:
    """Creates a basic Term Frequency sparse vector for Qdrant (which applies IDF)"""
    import re
    # Simple tokenization: lowercase, alpha-numeric
    tokens = re.findall(r'\w+', text.lower())
    freq = {}
    for t in tokens:
        # Hash token to a 32-bit integer for sparse index
        idx = abs(hash(t)) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1
    
    indices = []
    values = []
    for k, v in freq.items():
        indices.append(k)
        values.append(float(v))
    return SparseVector(indices=indices, values=values)


# ─────────────────────────────────────────────────────────────────────────────
# PDF Inspector — Detects if a PDF is digital or scanned
# ─────────────────────────────────────────────────────────────────────────────
def is_scanned_pdf(file_bytes: bytes) -> bool:
    """
    Returns True if the PDF appears to be a scanned image (no extractable text).
    Checks the first 3 pages — if none have text, it's likely a scan.
    """
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_to_check = min(3, len(reader.pages))
    for i in range(pages_to_check):
        text = reader.pages[i].extract_text()
        if text and text.strip():
            return False  # Found text — digital PDF
    return True  # No text — likely a scan


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous Connection Pools (For Celery Workers)
# ─────────────────────────────────────────────────────────────────────────────
import redis as sync_redis
import psycopg2
from psycopg2 import pool

_redis_conn = None
_pg_pool = None

def get_redis_conn():
    global _redis_conn
    if _redis_conn is None:
        redis_url = f"rediss://default:{password}@{host}:{port}/0?ssl_cert_reqs=none"
        _redis_conn = sync_redis.Redis.from_url(
            redis_url, 
            decode_responses=True
        )
    return _redis_conn

def get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        db_url = os.getenv(
            "DATABASE_URL_SYNC",
            "postgresql://postgres:password@localhost:5432/legal_rag"
        )
        _pg_pool = pool.SimpleConnectionPool(1, 10, db_url)
    return _pg_pool


# ─────────────────────────────────────────────────────────────────────────────
# Redis Progress Updater (Synchronous for Celery)
# ─────────────────────────────────────────────────────────────────────────────
def update_progress_sync(file_id: int, percent: int):
    """Updates the processing progress in Redis using a connection pool."""
    r = get_redis_conn()
    r.set(f"progress:{file_id}", percent, ex=600)


def update_postgres_status_sync(
    file_id: int,
    status: str,
    content: str = None,
    error: str = None,
):
    """Updates the file status in Postgres using a synchronous connection pool."""
    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            if content is not None:
                cur.execute(
                    "UPDATE files SET status=%s, content=%s WHERE id=%s",
                    (status, content, file_id),
                )
            elif error is not None:
                cur.execute(
                    "UPDATE files SET status=%s, error_message=%s WHERE id=%s",
                    (status, error, file_id),
                )
            else:
                cur.execute("UPDATE files SET status=%s WHERE id=%s", (status, file_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pg_pool.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Core Processing — Uses Your Existing Services
# ─────────────────────────────────────────────────────────────────────────────
def _process_file_core(file_id: int, org_id: str, filename: str, file_path: str):
    """
    Processes a PDF using the existing pipeline:
      1. parser.extract_from_pdf()  → List[{page, text}]
      2. chunker.chunk_text()       → List[{chunk_text, section_text, parent_id, page_number}]
      3. embedder.get_embedding()   → List[List[float]]
      4. Qdrant upsert              → tagged with file_id + org_id
    """
    try:
        # ── Step 1: Parse (using your existing parser.py) ────────────────────
        # parser.extract_from_pdf() expects a file-like object
        update_progress_sync(file_id, 5)
        
        # Open file natively from disk instead of RAM buffering
        with open(file_path, "rb") as f:
            pages_data = extract_from_pdf(f)

        if not pages_data:
            update_postgres_status_sync(
                file_id, "FAILED", error="No text could be extracted from PDF."
            )
            return

        # Build the full raw text for storage in Postgres
        full_text = "\n".join(p["text"] for p in pages_data)

        # ── Step 2: Chunk (using your existing hierarchical chunker.py) ──────
        # chunk_text() returns List[{chunk_text, section_text, parent_id, page_number, source_type}]
        update_progress_sync(file_id, 15)
        chunk_objects = chunk_text(pages_data)
        total_chunks = len(chunk_objects)

        if total_chunks == 0:
            update_postgres_status_sync(
                file_id, "FAILED", error="Chunking produced no results."
            )
            return

        print(f"[tasks] File {file_id}: {len(pages_data)} pages → {total_chunks} chunks")

        # ── Step 3: Embed + Upsert to Qdrant ────────────────────────────────
        # Process in batches of 20 to avoid huge single API calls
        qdrant = get_qdrant()
        ensure_collection_exists(qdrant)

        BATCH_SIZE = 20

        for batch_start in range(0, total_chunks, BATCH_SIZE):
            batch = chunk_objects[batch_start: batch_start + BATCH_SIZE]
            # embedder.get_embedding() expects List[str]
            texts = [c["chunk_text"] for c in batch]
            vectors = get_embedding(texts)

            if not vectors:
                # Embedding failed for this batch — skip but don't fail the whole file
                print(f"[tasks] Warning: embedding failed for batch {batch_start}")
                continue

            batch_points = []

            for i, (chunk, vector) in enumerate(zip(batch, vectors)):
                global_idx = batch_start + i
                
                # Build the sparse vector (Term Frequency)
                # We use both the chunk target text and the surrounding structure
                search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
                sparse_vec = compute_sparse_vector(search_text)

                batch_points.append(
                    PointStruct(
                        # Deterministic ID: same file + chunk index = same point
                        id=abs(hash(f"{file_id}_{global_idx}")) % (2**63),
                        vector={
                            "": vector,  # Default dense vector
                            "text-sparse": sparse_vec
                        },
                        payload={
                            # ── Isolation (ALWAYS filter by these in queries) ──
                            "file_id":      file_id,
                            "org_id":       org_id,
                            # ── Hierarchical context (from your chunker) ───────
                            "chunk_text":   chunk["chunk_text"],    # Small search target
                            "section_text": chunk.get("section_text", ""),  # Parent context
                            "parent_id":    chunk.get("parent_id", ""),
                            "source_type":  chunk.get("source_type", ""),   # structured_header | sliding_window
                            # ── Metadata ───────────────────────────────────────
                            "page_number":  chunk.get("page_number", 0),
                            "filename":     filename,
                        },
                    )
                )
            if batch_points:
                qdrant.upsert(collection_name=QDRANT_COLLECTION, points=batch_points)

            # Progress: 15% → 90% during embedding
            progress = 15 + int(((batch_start + BATCH_SIZE) / total_chunks) * 75)
            update_progress_sync(file_id, min(progress, 90))

        # ── Step 5: Mark as READY in Postgres ───────────────────────────────
        update_postgres_status_sync(file_id, "READY", content=full_text)
        update_progress_sync(file_id, 100)
        print(f"[tasks] File {file_id} ({filename}): READY ✓")

    except Exception as e:
        print(f"[tasks] File {file_id} FAILED: {e}")
        update_postgres_status_sync(file_id, "FAILED", error=str(e))
        raise  # Let Celery know so it can retry


# ─────────────────────────────────────────────────────────────────────────────
# Celery Task Definitions (Two Queues)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.tasks.process_digital_pdf",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="default",
)
def process_digital_pdf(self, file_id: int, org_id: str, filename: str, file_path: str):
    """
    Task for clean, digital PDFs. Runs on the fast "default" queue.
    file_path: Local absolute path to the streaming downloaded file.
    """
    try:
        _process_file_core(file_id, org_id, filename, file_path)
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        # Disk Cleanup: Always delete temp file to prevent disk exhaustion
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[tasks] Cleaned up temporary file: {file_path}")
        except Exception as e:
            print(f"[tasks] Warning: Failed to clean up {file_path}: {e}")


@celery_app.task(
    name="app.tasks.process_scanned_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    queue="ocr",
)
def process_scanned_pdf(self, file_id: int, org_id: str, filename: str, file_path: str):
    """
    Task for scanned/image PDFs. Runs on the slow "ocr" queue.
    This prevents a 50-page fax from blocking a fast digital contract.
    NOTE: Full Tesseract OCR can be added here later for true image-only PDFs.
    """
    try:
        _process_file_core(file_id, org_id, filename, file_path)
    except Exception as exc:
        raise self.retry(exc=exc)
    finally:
        # Disk Cleanup: Always delete temp file to prevent disk exhaustion
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"[tasks] Cleaned up temporary file: {file_path}")
        except Exception as e:
            print(f"[tasks] Warning: Failed to clean up {file_path}: {e}")
