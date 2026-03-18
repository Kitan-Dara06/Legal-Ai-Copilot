# app/tasks.py
#
# PURPOSE: The "Paralegal" — background tasks that process uploaded files.
#
# Uses the EXISTING pipeline:
#   parser.py        → extract_from_pdf()                    — extracts text per page (digital PDFs)
#   ocr_gemini.py    → ocr_pdf_path_to_markdown_pages()      — OCR scanned PDFs to MARKDOWN per page
#   chunker.py       → chunk_text()                          — hierarchical chunking (parent/child)
#   embedder.py      → get_embedding()                       — Cloudflare BGE-M3 / OpenRouter fallback
#
# For scanned PDFs (no extractable text), we use Gemini to extract structured Markdown
# that preserves document hierarchy (# Article, ## Clause, tables). This enables better
# semantic chunk boundaries and fewer hallucinations.
#
# Then upserts to Qdrant with file_id + org_id for isolation.
#
# Flow:
#   1. API uploads file → saves to Postgres (PENDING) → triggers this task
#   2. This task: Parse/OCR → Chunk → Embed → Upsert to Qdrant
#   3. Updates Postgres status to READY (or FAILED)
#   4. Updates Redis progress every batch so the UI can show "45% done"

import io
import logging
import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from app.config import get_database_url_sync, redis_disable_tls_verify
from app.logging_config import configure_logging
from app.services.chunker import chunk_text
from app.services.embedder import get_embedding
from app.services.ocr_gemini import GeminiOcrError, ocr_pdf_path_to_markdown_pages

# ── Your existing services (unchanged) ───────────────────────────────────────
from app.services.parser import extract_from_pdf
from app.worker import celery_app

configure_logging()
logger = logging.getLogger(__name__)

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")  # Required for Qdrant Cloud
QDRANT_COLLECTION = "legal_chunks"
# Keep this as int for SDK typing (and because QdrantClient.timeout expects int-like seconds).
QDRANT_TIMEOUT_SECONDS = int(float(os.getenv("QDRANT_TIMEOUT_SECONDS", "30")))

host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = (os.getenv("UPSTASH_PASSWORD") or "").strip()

# ── Gemini OCR Configuration ──────────────────────────────────────────────────
# Set GEMINI_API_KEY in .env.production (and locally in .env) to enable OCR.
# Model default lives in .env.example as GEMINI_MODEL=gemini-2.5-pro
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Qdrant Client (Synchronous — Celery doesn't use async)
# ─────────────────────────────────────────────────────────────────────────────
_qdrant_client = None


def get_qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        # Qdrant Cloud can be slow to respond during cold starts; increase HTTP timeouts.
        _qdrant_client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            timeout=QDRANT_TIMEOUT_SECONDS,
        )
    return _qdrant_client


def ensure_collection_exists(client: QdrantClient):
    """Creates the Qdrant collection if it doesn't already exist."""
    existing = [c.name for c in client.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(
                size=1024,  # BGE-M3 output size via OpenRouter
                distance=Distance.COSINE,
            ),
            sparse_vectors_config={
                "text-sparse": SparseVectorParams(modifier=Modifier.IDF)
            },
        )

        # Create an index for file_id so we can filter by it
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="file_id",
            field_schema=PayloadSchemaType.INTEGER,
        )
        # Create an index for org_id so we can filter by it
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="org_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
        # Create an index for filename so read_tool can filter by contract name
        client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="filename",
            field_schema=PayloadSchemaType.KEYWORD,
        )


# ── Sparse Vector builder ────────────────────────────────────────────────────
def compute_sparse_vector(text: str) -> SparseVector:
    """Creates a basic Term Frequency sparse vector for Qdrant (which applies IDF)"""
    import hashlib
    import re

    # Simple tokenization: lowercase, alpha-numeric
    tokens = re.findall(r"\w+", text.lower())
    freq = {}
    for t in tokens:
        # Stable hash token to a 32-bit integer for sparse index
        hex_digest = hashlib.md5(t.encode("utf-8")).hexdigest()
        idx = int(hex_digest, 16) % (2**31 - 1)
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

    NOTE: This is a heuristic. In production we still rely on the OCR queue
    for scans and the digital queue for normal PDFs.
    """
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_to_check = min(3, len(reader.pages))
    for i in range(pages_to_check):
        text = reader.pages[i].extract_text()
        if text and text.strip():
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous Connection Pools (For Celery Workers)
# ─────────────────────────────────────────────────────────────────────────────
import redis as sync_redis
from psycopg2 import pool

_redis_conn = None
_pg_pool = None


def get_redis_conn():
    global _redis_conn
    if _redis_conn is None:
        if not host or not password:
            raise RuntimeError(
                "Upstash Redis credentials are missing. Set UPSTASH_HOST and UPSTASH_PASSWORD."
            )

        base_redis_url = f"rediss://default:{password}@{host}:{port}/0"
        if redis_disable_tls_verify():
            redis_url = base_redis_url + "?ssl_cert_reqs=none"
        else:
            redis_url = base_redis_url
        _redis_conn = sync_redis.Redis.from_url(redis_url, decode_responses=True)
    return _redis_conn


def get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        db_url = get_database_url_sync()
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
    content: str | None = None,
    error: str | None = None,
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


def _gemini_ocr_pdf_to_markdown(pdf_path: str) -> str:
    """
    Backwards-compatible helper: OCR a scanned PDF into Markdown.

    NOTE: We now prefer per-page Markdown via app.services.ocr_gemini so chunking has
    better boundaries ("new chunk at headers") and less cross-clause leakage.
    This function keeps the old call-site shape for minimal refactors.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing. Set it in your environment / .env.production to enable OCR."
        )
    if not GEMINI_MODEL:
        raise RuntimeError(
            "GEMINI_MODEL is missing/empty. Set GEMINI_MODEL (e.g. gemini-2.5-pro)."
        )

    # Ensure we never pass None to the OCR helper (type-checker + runtime safety).
    pages = ocr_pdf_path_to_markdown_pages(
        pdf_path, api_key=str(GEMINI_API_KEY), model=str(GEMINI_MODEL)
    )

    # Join pages with explicit delimiters to preserve page boundaries in the stored content.
    out = []
    for p in pages:
        out.append(f"===PAGE {p['page']}===\n{p.get('text', '').strip()}")
    return "\n\n".join(out).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Core Processing — Uses Your Existing Services
# ─────────────────────────────────────────────────────────────────────────────
def _process_file_core(
    file_id: int,
    org_id: str,
    filename: str,
    file_path: str,
    *,
    pages_data_override: Optional[List[Dict]] = None,
    full_text_override: Optional[str] = None,
):
    """
    Processes a PDF using the existing pipeline:
      1. Extract pages_data: List[{page, text}]
         - Digital PDFs: parser.extract_from_pdf()
         - Scanned PDFs: Gemini OCR -> Markdown -> pages_data_override
      2. chunker.chunk_text()       → List[{chunk_text, section_text, parent_id, page_number}]
      3. embedder.get_embedding()   → List[List[float]]
      4. Qdrant upsert              → tagged with file_id + org_id
    """
    try:
        # ── Step 1: Parse/OCR into pages_data ────────────────────────────────
        update_progress_sync(file_id, 5)

        if pages_data_override is not None:
            pages_data = pages_data_override
            full_text = (
                full_text_override
                if full_text_override is not None
                else "\n".join(p.get("text", "") for p in pages_data if p.get("text"))
            )
        else:
            # Digital path: Open file natively from disk instead of RAM buffering
            with open(file_path, "rb") as f:
                pages_data = extract_from_pdf(f)

            if not pages_data:
                update_postgres_status_sync(
                    file_id, "FAILED", error="No text could be extracted from PDF."
                )
                return

            # Build the full raw text for storage in Postgres
            full_text = "\n".join(p["text"] for p in pages_data)

        if not pages_data:
            update_postgres_status_sync(
                file_id, "FAILED", error="No text could be extracted from PDF."
            )
            return

        # ── Step 2: Chunk (using your existing hierarchical chunker.py) ──────
        update_progress_sync(file_id, 15)
        chunk_objects = chunk_text(pages_data)
        total_chunks = len(chunk_objects)

        if total_chunks == 0:
            update_postgres_status_sync(
                file_id, "FAILED", error="Chunking produced no results."
            )
            return

        print(
            f"[tasks] File {file_id}: {len(pages_data)} pages → {total_chunks} chunks"
        )

        # ── Step 3: Embed + Upsert to Qdrant ────────────────────────────────
        print(f"[tasks] File {file_id}: Connecting to Qdrant...")
        qdrant = get_qdrant()
        print(f"[tasks] File {file_id}: Ensuring collection exists...")
        ensure_collection_exists(qdrant)
        print(
            f"[tasks] File {file_id}: Ready to upsert {total_chunks} chunks in batches"
        )

        BATCH_SIZE = 20

        for batch_start in range(0, total_chunks, BATCH_SIZE):
            batch = chunk_objects[batch_start : batch_start + BATCH_SIZE]
            texts = [c["chunk_text"] for c in batch]
            vectors = get_embedding(texts)

            if not vectors:
                print(f"[tasks] Warning: embedding failed for batch {batch_start}")
                continue

            batch_points = []

            for i, (chunk, vector) in enumerate(zip(batch, vectors)):
                global_idx = batch_start + i

                search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
                sparse_vec = compute_sparse_vector(search_text)

                import uuid

                batch_points.append(
                    PointStruct(
                        id=str(
                            uuid.uuid5(
                                uuid.NAMESPACE_OID, f"{org_id}_{file_id}_{global_idx}"
                            )
                        ),
                        vector={
                            "": vector,
                            "text-sparse": sparse_vec,
                        },
                        payload={
                            "file_id": file_id,
                            "org_id": org_id,
                            "chunk_text": chunk["chunk_text"],
                            "section_text": chunk.get("section_text", ""),
                            "parent_id": chunk.get("parent_id", ""),
                            "source_type": chunk.get("source_type", ""),
                            "page_number": chunk.get("page_number", 0),
                            "filename": filename,
                        },
                    )
                )

            if batch_points:
                qdrant.upsert(collection_name=QDRANT_COLLECTION, points=batch_points)

            progress = 15 + int(((batch_start + BATCH_SIZE) / total_chunks) * 75)
            update_progress_sync(file_id, min(progress, 90))

        # ── Step 5: Mark as READY in Postgres ───────────────────────────────
        update_postgres_status_sync(file_id, "READY", content=full_text)
        update_progress_sync(file_id, 100)
        print(f"[tasks] File {file_id} ({filename}): READY ✓")

    except Exception as e:
        print(f"[tasks] File {file_id} FAILED: {e}")
        update_postgres_status_sync(file_id, "FAILED", error=str(e))
        raise


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
def process_digital_pdf(self, file_id: int, org_id: str, filename: str, blob_name: str):
    """
    Task for clean, digital PDFs. Runs on the fast "default" queue.
    blob_name: The R2/S3 object key.
    """
    import os
    import uuid

    from app.services.object_storage import delete_file_from_gcs, download_file_from_gcs

    local_temp_path = f"/tmp/{uuid.uuid4().hex}.pdf"
    try:
        print(f"[tasks] Downloading {blob_name} to {local_temp_path}...")
        download_file_from_gcs(blob_name, local_temp_path)

        _process_file_core(file_id, org_id, filename, local_temp_path)

        # Success Cleanup
        delete_file_from_gcs(blob_name)
    except Exception as exc:
        safe_exc = RuntimeError(
            f"process_digital_pdf failed with {type(exc).__name__}: {exc}"
        )
        raise self.retry(exc=safe_exc)
    finally:
        # Disk Cleanup: ALWAYS delete temp file to prevent disk exhaustion
        try:
            if os.path.exists(local_temp_path):
                os.remove(local_temp_path)
                print(f"[tasks] Cleaned up temporary file: {local_temp_path}")
        except Exception as e:
            print(f"[tasks] Warning: Failed to clean up {local_temp_path}: {e}")


@celery_app.task(
    name="app.tasks.process_scanned_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    queue="ocr",
)
def process_scanned_pdf(self, file_id: int, org_id: str, filename: str, blob_name: str):
    """
    Task for scanned/image PDFs. Runs on the slow "ocr" queue.

    Uses Gemini Vision OCR to extract structured Markdown, then runs the same
    chunk/embed/upsert pipeline as digital PDFs.
    """
    import os
    import uuid

    from app.services.object_storage import delete_file_from_gcs, download_file_from_gcs

    local_temp_path = f"/tmp/{uuid.uuid4().hex}.pdf"
    try:
        print(f"[tasks] Downloading {blob_name} to {local_temp_path}...")
        download_file_from_gcs(blob_name, local_temp_path)

        if not GEMINI_API_KEY:
            raise RuntimeError(
                "Scanned PDF OCR requires GEMINI_API_KEY. Set it in .env.production."
            )

        # ── OCR with Gemini → Markdown (per page) ────────────────────────────
        update_progress_sync(file_id, 8)
        try:
            pages_data = ocr_pdf_path_to_markdown_pages(
                local_temp_path, api_key=GEMINI_API_KEY, model=GEMINI_MODEL
            )
        except GeminiOcrError as e:
            update_postgres_status_sync(
                file_id,
                "FAILED",
                error=f"Gemini OCR failed: {str(e)}",
            )
            return

        # Normalize / drop empty pages (do not fabricate content)
        pages_data = [
            {"page": int(p.get("page") or 1), "text": (p.get("text") or "").strip()}
            for p in pages_data
            if (p.get("text") or "").strip()
        ]

        if not pages_data:
            update_postgres_status_sync(
                file_id,
                "FAILED",
                error="Gemini OCR returned empty text for scanned PDF.",
            )
            return

        # Store a single markdown blob with page delimiters
        full_md = "\n\n".join(
            [f"===PAGE {p['page']}===\n{p['text']}" for p in pages_data]
        ).strip()

        _process_file_core(
            file_id,
            org_id,
            filename,
            local_temp_path,
            pages_data_override=pages_data,
            full_text_override=full_md,
        )

        # Success Cleanup
        delete_file_from_gcs(blob_name)

    except Exception as exc:
        safe_exc = RuntimeError(
            f"process_scanned_pdf failed with {type(exc).__name__}: {exc}"
        )
        raise self.retry(exc=safe_exc)
    finally:
        try:
            if os.path.exists(local_temp_path):
                os.remove(local_temp_path)
                print(f"[tasks] Cleaned up temporary file: {local_temp_path}")
        except Exception as e:
            print(f"[tasks] Warning: Failed to clean up {local_temp_path}: {e}")


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def qdrant_heartbeat(self):
    """
    Pings the Qdrant Cloud cluster periodically to prevent it from spinning down
    due to inactivity. A simple `get_collections()` call is enough.
    """
    try:
        print("💓 Pinging Qdrant to keep cluster hot...")
        client = get_qdrant()
        collections = client.get_collections()
        print(
            f"💓 Qdrant ping successful. Found {len(collections.collections)} collections."
        )
        return "Ping successful"
    except Exception as exc:
        print(f"❌ Qdrant ping failed: {exc}")
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# Dead Letter Queue — task_failure signal
# Celery has no built-in DLQ. This signal fires whenever a task exhausts all
# retries. We log it as structured JSON so it's visible in log aggregators.
# ─────────────────────────────────────────────────────────────────────────────
from celery.signals import task_failure  # noqa: E402


@task_failure.connect
def handle_task_failure(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **kw,
):
    logger.error(
        "Celery task permanently failed",
        extra={
            "task_name": sender.name if sender else "unknown",
            "task_id": task_id,
            "exception_type": type(exception).__name__ if exception else "unknown",
            "exception_msg": str(exception)[:500] if exception else "",
            "task_args": str(args)[:200] if args else "",
            "task_kwargs": str(kwargs)[:200] if kwargs else "",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sweep: detect tasks stuck in PENDING for > 30 min and mark them FAILED.
# This catches cases where the Celery worker crashed before even starting the
# task (message was consumed from the broker but worker died mid-flight).
# ─────────────────────────────────────────────────────────────────────────────
@celery_app.task(name="app.tasks.sweep_failed_tasks")
def sweep_failed_tasks():
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE files
                SET status = 'FAILED',
                    error_message = 'Task timed out: stuck in PENDING for >30 min. Re-upload or use /reprocess.'
                WHERE status = 'PENDING'
                  AND upload_date < %s
                RETURNING id, filename
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
        conn.commit()

        if rows:
            for file_id, filename in rows:
                logger.warning(
                    "Swept stuck PENDING task",
                    extra={"file_id": file_id, "file_name": filename},
                )
            logger.info(
                f"sweep_failed_tasks: marked {len(rows)} stuck file(s) as FAILED"
            )
        else:
            logger.info("sweep_failed_tasks: no stuck tasks found")

    except Exception as e:
        conn.rollback()
        logger.error(f"sweep_failed_tasks error: {e}")
    finally:
        pg_pool.putconn(conn)
