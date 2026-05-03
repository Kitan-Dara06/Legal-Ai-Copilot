# app/tasks.py
#
# PURPOSE: The "Paralegal" — background tasks that process uploaded files.
#
# Unified Lex Pipeline (Stage 2):
#   1. Parses with LegalDocumentParser (or Gemini OCR for scans)
#   2. Chunks with ClauseChunker
#   3. Runs parallel intelligence extraction via asyncio.gather:
#      - Multi-vector embedding (Voyage + BGE + SPLADE)
#      - Defined terms extraction
#      - Cross-reference graph extraction (Neo4j)
#      - Deadline extraction

import io
import logging
import os
import uuid
import asyncio
from typing import Dict, List, Optional

from dotenv import load_dotenv

from app.config import get_database_url_sync, redis_disable_tls_verify
from app.logging_config import configure_logging
from app.services.ocr_gemini import GeminiOcrError, ocr_pdf_path_to_markdown_pages
from app.worker import celery_app

# ── New Stage 2 Intelligence Extractors ──────────────────────────────────────
from app.services.ingestion.parser import LegalDocumentParser
from app.services.ingestion.chunker import ClauseChunker
from app.services.ingestion.embedder import LegalEmbedder
from app.services.ingestion.terms_extractor import DefinedTermExtractor
from app.services.ingestion.graph_extractor import LLMReferenceParser, DependencyGraph
from app.services.ingestion.deadline_extractor import DeadlineExtractor

configure_logging()
logger = logging.getLogger(__name__)

load_dotenv()

host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = (os.getenv("UPSTASH_PASSWORD") or "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro").strip()

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

def update_progress_sync(document_id: str, percent: int):
    """Updates the processing progress in Redis."""
    r = get_redis_conn()
    r.set(f"progress:{document_id}", percent, ex=600)

def update_postgres_status_sync(
    document_id: str,
    status: str,
    error: str | None = None,
    stages_complete: dict | None = None
):
    """Updates the document status in Postgres using a synchronous connection pool."""
    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        import json
        with conn.cursor() as cur:
            if stages_complete is not None:
                cur.execute(
                    "UPDATE documents SET status=%s, intelligence_stages_complete=%s WHERE id=%s",
                    (status, json.dumps(stages_complete), document_id),
                )
            elif error is not None:
                cur.execute(
                    "UPDATE documents SET status=%s, error_message=%s WHERE id=%s",
                    (status, error, document_id),
                )
            else:
                cur.execute("UPDATE documents SET status=%s WHERE id=%s", (status, document_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pg_pool.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Async Pipeline Runner
# ─────────────────────────────────────────────────────────────────────────────
async def run_intelligence_pipeline_async(
    document_id: uuid.UUID,
    workspace_id: uuid.UUID,
    org_id: uuid.UUID,
    filename: str,
    chunks: List[Dict]
):
    """
    Runs the intelligence extraction tasks in parallel using asyncio.
    """
    embedder = LegalEmbedder()
    terms_extractor = DefinedTermExtractor()
    deadline_extractor = DeadlineExtractor()
    graph_extractor = LLMReferenceParser()
    neo4j_graph = DependencyGraph()

    # Step A: Run synchronous Embedder inside an executor
    loop = asyncio.get_running_loop()
    
    async def task_embed():
        logger.info(f"[{document_id}] Starting embedding...")
        await loop.run_in_executor(None, embedder.index_document, filename, chunks)
        return "embedding_complete"

    async def task_terms():
        logger.info(f"[{document_id}] Starting terms extraction...")
        await terms_extractor.extract_and_store(chunks, document_id, workspace_id, org_id)
        return "terms_complete"

    async def task_deadlines():
        logger.info(f"[{document_id}] Starting deadlines extraction...")
        await deadline_extractor.extract_and_store(chunks, document_id, workspace_id, org_id)
        return "deadlines_complete"

    async def task_graph():
        logger.info(f"[{document_id}] Starting graph extraction...")
        # Resolve references (LLM)
        resolved_chunks = await loop.run_in_executor(
            None, 
            graph_extractor.resolve_references, 
            chunks, 
            filename, 
            neo4j_graph
        )
        # Build neo4j graph
        await loop.run_in_executor(None, neo4j_graph.build_graph, resolved_chunks, filename)
        neo4j_graph.close()
        return "graph_complete"

    # Run them all concurrently
    results = await asyncio.gather(
        task_embed(),
        task_terms(),
        task_deadlines(),
        task_graph(),
        return_exceptions=True
    )
    
    stages = {}
    for res in results:
        if isinstance(res, Exception):
            logger.error(f"[{document_id}] Task failed: {res}")
        else:
            stages[res] = True
            
    return stages


# ─────────────────────────────────────────────────────────────────────────────
# Core Processing
# ─────────────────────────────────────────────────────────────────────────────
def _process_document_core(
    document_id_str: str,
    workspace_id_str: str,
    org_id_str: str,
    filename: str,
    file_path: str,
    *,
    pages_data_override: Optional[List[Dict]] = None,
):
    document_id = uuid.UUID(document_id_str)
    workspace_id = uuid.UUID(workspace_id_str)
    org_id = uuid.UUID(org_id_str)
    
    try:
        update_progress_sync(document_id_str, 5)
        parser = LegalDocumentParser()

        # 1. Parse
        if pages_data_override is not None:
            raw_blocks = parser.parse_markdown(pages_data_override)
        else:
            if file_path.lower().endswith(".pdf"):
                raw_blocks = parser.parse_pdf(file_path)
            else:
                raw_blocks = parser.parse_docx(file_path)

        if not raw_blocks:
            update_postgres_status_sync(document_id_str, "FAILED", error="No text could be extracted.")
            return

        update_progress_sync(document_id_str, 20)

        # 2. Chunk
        chunker = ClauseChunker(body_font_size=parser.body_font_size)
        chunks = chunker.build_chunks(raw_blocks)
        
        if not chunks:
            update_postgres_status_sync(document_id_str, "FAILED", error="Chunking produced no results.")
            return

        # Add document metadata to chunks
        for chunk in chunks:
            chunk["document_name"] = filename

        update_progress_sync(document_id_str, 40)

        # 3. Parallel Intelligence Pipelines
        stages_complete = asyncio.run(
            run_intelligence_pipeline_async(
                document_id, workspace_id, org_id, filename, chunks
            )
        )

        update_progress_sync(document_id_str, 90)

        # 4. Mark Ready
        update_postgres_status_sync(document_id_str, "READY", stages_complete=stages_complete)
        update_progress_sync(document_id_str, 100)
        logger.info(f"[{document_id_str}] Document Processing Complete")

    except Exception as e:
        logger.error(f"[{document_id_str}] FAILED: {e}")
        update_postgres_status_sync(document_id_str, "FAILED", error=str(e))
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
def process_digital_pdf(self, document_id: str, workspace_id: str, org_id: str, filename: str, blob_name: str):
    import os
    from app.services.object_storage import delete_file_from_gcs, download_file_from_gcs

    local_temp_path = f"/tmp/{uuid.uuid4().hex}_{filename}"
    try:
        download_file_from_gcs(blob_name, local_temp_path)
        _process_document_core(document_id, workspace_id, org_id, filename, local_temp_path)
        delete_file_from_gcs(blob_name)
    except Exception as exc:
        safe_exc = RuntimeError(f"Task failed: {exc}")
        raise self.retry(exc=safe_exc)
    finally:
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)


@celery_app.task(
    name="app.tasks.process_scanned_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    queue="ocr",
)
def process_scanned_pdf(self, document_id: str, workspace_id: str, org_id: str, filename: str, blob_name: str):
    import os
    from app.services.object_storage import delete_file_from_gcs, download_file_from_gcs

    local_temp_path = f"/tmp/{uuid.uuid4().hex}_{filename}"
    try:
        download_file_from_gcs(blob_name, local_temp_path)
        
        if not GEMINI_API_KEY:
            raise RuntimeError("Scanned PDF OCR requires GEMINI_API_KEY.")

        pages_data = ocr_pdf_path_to_markdown_pages(
            local_temp_path, api_key=GEMINI_API_KEY, model=GEMINI_MODEL
        )
        
        pages_data = [
            {"page": int(p.get("page") or 1), "text": (p.get("text") or "").strip()}
            for p in pages_data if (p.get("text") or "").strip()
        ]

        if not pages_data:
            raise RuntimeError("Gemini OCR returned empty text.")

        _process_document_core(
            document_id, workspace_id, org_id, filename, local_temp_path, pages_data_override=pages_data
        )
        delete_file_from_gcs(blob_name)
    except Exception as exc:
        safe_exc = RuntimeError(f"Task failed: {exc}")
        raise self.retry(exc=safe_exc)
    finally:
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)


@celery_app.task(name="app.tasks.cleanup_stale_data")
def cleanup_stale_data():
    """
    Periodic maintenance task to purge expired invites and stale temporary files.
    """
    from datetime import datetime, timezone
    import time

    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    deleted_invites = 0
    deleted_tmp = 0

    try:
        with conn.cursor() as cur:
            # 1. Purge expired invites that were never accepted
            cur.execute(
                "DELETE FROM organization_invites WHERE expires_at < %s AND is_accepted = FALSE",
                (datetime.now(timezone.utc),)
            )
            deleted_invites = cur.rowcount
        conn.commit()

        # 2. Sweep /tmp for processing remnants older than 2 hours
        tmp_dir = "/tmp"
        now = time.time()
        for filename in os.listdir(tmp_dir):
            # Target files created by our PDF processing tasks
            if filename.startswith("temp_") or "_digital_" in filename or "_scanned_" in filename:
                file_path = os.path.join(tmp_dir, filename)
                try:
                    if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > 7200:
                        os.remove(file_path)
                        deleted_tmp += 1
                except Exception:
                    continue

        logger.info(
            "Maintenance Task: Purged %d expired invites and %d stale temp files.",
            deleted_invites, deleted_tmp
        )
    except Exception as e:
        conn.rollback()
        logger.error("Maintenance Task Failed: %s", e)
    finally:
        pg_pool.putconn(conn)
