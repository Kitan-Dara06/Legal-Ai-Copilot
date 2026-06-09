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
#      - Cross-reference graph extraction (FalkorDB)
#      - Deadline extraction

import asyncio
import io
import logging
import os
import uuid
from typing import Dict, List, Optional

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from dotenv import load_dotenv

from app.celery_app import celery_app
from app.config import get_database_url_sync, redis_disable_tls_verify
from app.logging_config import configure_logging
from app.redis_client import acquire_llm_slot, release_llm_slot


class LeaseExhaustedError(Exception):
    """Raised when LLM concurrency limit is reached."""

    pass


configure_logging()
logger = logging.getLogger(__name__)

load_dotenv()

host = os.getenv("UPSTASH_HOST")
port = os.getenv("UPSTASH_PORT", "6379")
password = (os.getenv("UPSTASH_PASSWORD") or "").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()


class AsyncTask(Task):
    """
    Base Celery task that submits async work to the persistent event loop.

    Use self.run_async(coro) instead of asyncio.run(coro) to avoid
    creating/destroying event loops per task.
    """

    abstract = True

    def run_async(self, coro):
        from app.worker import get_worker_loop

        loop = get_worker_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()


def tasks_smoke_check(*, include_gemini: bool = False) -> dict:
    """
    Proactively validate that the heavy ingestion/task dependencies can import.

    Use this at API startup (and/or readiness checks) to surface failures *before*
    real users hit upload/task routes.
    """
    # Imports that commonly fail due to missing system libs / wheels / model deps.
    from app.services.ingestion.chunker import ClauseChunker  # noqa: F401
    from app.services.ingestion.deadline_extractor import (
        DeadlineExtractor,  # noqa: F401  # noqa: F401
    )
    from app.services.ingestion.graph_extractor import (
        DependencyGraph,
        LLMReferenceParser,
    )  # noqa: F401
    from app.services.ingestion.parser import LegalDocumentParser  # noqa: F401

    # A tiny instantiation “touch” catches missing model downloads / init errors.
    _ = LegalDocumentParser()
    _ = ClauseChunker(body_font_size=12)

    if include_gemini:
        from app.services.ocr_gemini import ocr_pdf_path_to_markdown_pages  # noqa: F401
        # We do not call Gemini here; import+symbol resolution is enough.

    return {"ok": True}


_WORKER_WARMED = False


def warmup_heavy_dependencies(*, include_gemini: bool = False) -> dict:
    """
    Celery-only hot start.

    This intentionally runs in worker processes (not the web server) to keep the
    API lightweight while keeping expensive imports/initialization warm.
    """
    global _WORKER_WARMED
    tasks_smoke_check(include_gemini=include_gemini)
    _WORKER_WARMED = True
    return {"ok": True, "warmed": True}


@celery_app.task(name="app.tasks.ping")
def ping() -> dict:
    return {"ok": True}


@celery_app.task(name="app.tasks.warmup")
def warmup(*, include_gemini: bool = False) -> dict:
    return warmup_heavy_dependencies(include_gemini=include_gemini)


@celery_app.task(name="app.tasks.warm_status")
def warm_status() -> dict:
    return {"ok": True, "warmed": bool(_WORKER_WARMED)}


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous Connection Pools (For Celery Workers)
# ─────────────────────────────────────────────────────────────────────────────
import redis as sync_redis
from psycopg2 import pool

_redis_conn = None
_pg_pool = None


def _reset_pg_pool():
    """Reset sync pg pool and redis conn after fork (invalidates inherited connections)."""
    global _pg_pool, _redis_conn
    _pg_pool = None
    _redis_conn = None


try:
    os.register_at_fork(after_in_child=_reset_pg_pool)
except AttributeError:
    pass


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
        # ThreadedConnectionPool is thread-safe (SimpleConnectionPool is NOT).
        # max=12: allows concurrency=4 workers + deadline scanner + beat + 5 headroom.
        # Supabase free tier allows 25 total connections; async API pool uses ~5.
        _pg_pool = pool.ThreadedConnectionPool(1, 12, db_url)
    return _pg_pool


def _get_valid_conn(pg_pool) -> tuple:
    """
    Get a connection from the pool and validate it is still alive.
    If stale (closed server-side), discard and retry once.
    Resets transaction state after validation to prevent "cursor already closed".
    """
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()  # clear lingering transaction from the ping
        return conn, pg_pool
    except Exception:
        # Connection is stale — discard and get a fresh one
        pg_pool.putconn(conn, close=True)
        conn = pg_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
        return conn, pg_pool


def update_progress_sync(document_id: str, percent: int):
    """Updates the processing progress in Redis."""
    r = get_redis_conn()
    r.set(f"progress:{document_id}", percent, ex=600)


def update_postgres_status_sync(
    document_id: str,
    status: str,
    error: str | None = None,
    stages_complete: dict | None = None,
):
    """Updates the document status in Postgres using a synchronous connection pool."""
    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        import json

        with conn.cursor() as cur:
            if stages_complete is not None:
                cur.execute(
                    "UPDATE documents SET status=%s, intelligence_stages_complete=%s WHERE id=%s RETURNING workspace_id",
                    (status, json.dumps(stages_complete), document_id),
                )
            elif error is not None:
                cur.execute(
                    "UPDATE documents SET status=%s, error_message=%s WHERE id=%s RETURNING workspace_id",
                    (status, error, document_id),
                )
            else:
                cur.execute(
                    "UPDATE documents SET status=%s WHERE id=%s RETURNING workspace_id",
                    (status, document_id),
                )

            row = cur.fetchone()
            if row:
                workspace_id = str(row[0])
                cur.execute(
                    """
                    SELECT 1 FROM documents
                    WHERE workspace_id = %s
                      AND status IN ('PENDING', 'PROCESSING')
                    LIMIT 1
                    """,
                    (workspace_id,),
                )
                has_pending = cur.fetchone() is not None
                new_status = "PENDING" if has_pending else "READY"

                cur.execute(
                    "UPDATE workspaces SET intelligence_status = %s WHERE id = %s",
                    (new_status, workspace_id),
                )
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
    chunks: List[Dict],
):
    """
    Runs the intelligence extraction tasks in parallel using asyncio.
    """
    # Heavy imports live here so importing `app.tasks` is safe for the API.
    from app.services.ingestion.deadline_extractor import DeadlineExtractor
    from app.services.ingestion.embedder import LegalEmbedder
    from app.services.ingestion.graph_extractor import (
        DependencyGraph,
        LLMReferenceParser,
    )
    from app.services.ingestion.terms_extractor import DefinedTermExtractor

    embedder = LegalEmbedder()
    terms_extractor = DefinedTermExtractor()
    deadline_extractor = DeadlineExtractor()
    graph_extractor = LLMReferenceParser()
    falkordb_graph = DependencyGraph()

    # Step A: Run synchronous Embedder inside an executor
    loop = asyncio.get_running_loop()

    async def task_embed():
        logger.info(f"[{document_id}] Starting embedding...")
        # Add metadata fields to each chunk for Qdrant payload
        for chunk in chunks:
            chunk["org_id"] = str(org_id)
            chunk["workspace_id"] = str(workspace_id)
            chunk["file_id"] = str(document_id)
            chunk["filename"] = filename
        await loop.run_in_executor(None, embedder.index_document, filename, chunks)
        return "embedding_complete"

    async def task_terms():
        logger.info(f"[{document_id}] Starting terms extraction...")
        await terms_extractor.extract_and_store(
            chunks, document_id, workspace_id, org_id
        )
        # Flush stage completion + trigger workspace sweep if all docs done
        await loop.run_in_executor(
            None,
            _flush_stage_and_maybe_sweep,
            str(document_id),
            str(workspace_id),
            "defined_terms",
            "app.tasks.resolve_defined_term_conflicts",
        )
        return "terms_complete"

    async def task_deadlines():
        logger.info(f"[{document_id}] Starting deadlines extraction...")
        await deadline_extractor.extract_and_store(
            chunks, document_id, workspace_id, org_id
        )
        # Flush stage completion + trigger workspace sweep if all docs done
        await loop.run_in_executor(
            None,
            _flush_stage_and_maybe_sweep,
            str(document_id),
            str(workspace_id),
            "deadlines",
            "app.tasks.resolve_deadline_conflicts",
        )
        return "deadlines_complete"

    async def task_graph():
        logger.info(f"[{document_id}] Starting graph extraction...")
        # Resolve references (LLM)
        resolved_chunks = await graph_extractor.resolve_references(
            chunks,
            filename,
            falkordb_graph,
            org_id=org_id,
            workspace_id=str(workspace_id),
        )
        # Build FalkorDB graph
        await loop.run_in_executor(
            None,
            falkordb_graph.build_graph,
            resolved_chunks,
            filename,
            str(workspace_id),
        )
        falkordb_graph.close()
        return "graph_complete"

    # Run them all concurrently
    results = await asyncio.gather(
        task_embed(),
        task_terms(),
        task_deadlines(),
        task_graph(),
        return_exceptions=True,
    )

    stages = {}
    for res in results:
        if isinstance(res, Exception):
            logger.error(f"[{document_id}] Task failed: {res}")
        else:
            stages[res] = True

    return stages


# ─────────────────────────────────────────────────────────────────────────────
# Stage-Level Keyword Pre-Filter Helpers
# ─────────────────────────────────────────────────────────────────────────────


def is_definition_chunk(chunk: dict) -> bool:
    """
    Pre-filter: returns True if the chunk's hierarchy indicates it belongs
    to a definitions/glossary/interpretation section.

    This reduces LLM calls from hundreds down to ~15 per document.
    """
    hierarchy = chunk.get("hierarchy", [])
    keywords = {"definition", "definitions", "glossary", "interpretation"}
    for ancestor in hierarchy:
        if any(kw in ancestor.lower() for kw in keywords):
            return True
    # Also check the text itself for a strong signal
    text_start = (chunk.get("text", "") or "")[:200].lower()
    if any(kw in text_start for kw in ["definition", "definitions", "glossary"]):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Stage-Level Completion Flush + Workspace Sweep Trigger
# ─────────────────────────────────────────────────────────────────────────────

# Thread-local list to defer Celery enqueue until after DB commit succeeds
import threading

_pending_sweeps = threading.local()


def _flush_stage_and_maybe_sweep(
    document_id: str,
    workspace_id: str,
    stage_key: str,
    sweep_task_name: str,
):
    """
    Atomic operation called after a pipeline stage finishes for a document:

    1. Persist the stage completion in `intelligence_stages_complete` JSONB.
    2. Check if ALL non-failed documents in the workspace have completed this stage.
    3. If yes, update workspace status and defer enqueue of the sweep Celery task.

    Designed to run inside `run_in_executor` (synchronous psycopg2).
    """
    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            # Step 1: Mark this stage as complete for this document
            cur.execute(
                """
                UPDATE documents
                SET intelligence_stages_complete =
                    COALESCE(intelligence_stages_complete, '{}'::jsonb)
                    || jsonb_build_object(%s, true)
                WHERE id = %s
                """,
                (stage_key, document_id),
            )

            # Step 2: Check if any non-failed doc in the workspace is missing this stage
            cur.execute(
                """
                SELECT 1 FROM documents
                WHERE workspace_id = %s
                  AND status != 'FAILED'
                  AND (
                    intelligence_stages_complete IS NULL
                    OR (intelligence_stages_complete->>%s)::boolean IS DISTINCT FROM true
                  )
                LIMIT 1
                """,
                (workspace_id, stage_key),
            )
            all_complete = cur.fetchone() is None

            # Step 3: If all docs are done with this stage, trigger sweep
            if all_complete:
                cur.execute(
                    "UPDATE workspaces SET intelligence_status = 'READY' WHERE id = %s",
                    (workspace_id,),
                )
                # Defer enqueue until after commit
                if not hasattr(_pending_sweeps, "tasks"):
                    _pending_sweeps.tasks = []
                _pending_sweeps.tasks.append((sweep_task_name, workspace_id))

        conn.commit()

        # Enqueue sweep tasks after successful commit
        if hasattr(_pending_sweeps, "tasks"):
            for task_name, ws_id in _pending_sweeps.tasks:
                celery_app.send_task(task_name, args=[ws_id])
                logger.info("[sweep] Enqueued %s for workspace %s", task_name, ws_id)
            _pending_sweeps.tasks = []

    except Exception as e:
        conn.rollback()
        logger.error(
            "[flush_stage] Failed for doc=%s stage=%s: %s",
            document_id,
            stage_key,
            e,
        )
        raise
    finally:
        pg_pool.putconn(conn)


@celery_app.task(
    name="app.tasks.resolve_defined_term_conflicts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def resolve_defined_term_conflicts(self, workspace_id: str):
    """
    Workspace-wide conflict sweep for defined terms.

    Finds terms that appear in multiple documents with differing definitions,
    then flags all conflicting records with `conflict_flag = TRUE` and a
    human-readable description.

    SRS: Cross-document conflict detection runs after all workspace documents
    have completed the 'defined_terms' extraction stage.
    """
    logger.info("[resolve_defined_term_conflicts] Sweeping workspace %s", workspace_id)
    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            # Find terms defined in multiple docs with different definitions
            cur.execute(
                """
                SELECT a.id, a.term, a.definition, a.source_document_id,
                       b.id, b.source_document_id, b.definition
                FROM defined_terms_registry a
                JOIN defined_terms_registry b
                  ON a.workspace_id = b.workspace_id
                 AND a.term = b.term
                 AND a.source_document_id < b.source_document_id
                 AND a.definition != b.definition
                WHERE a.workspace_id = %s
                """,
                (workspace_id,),
            )
            rows = cur.fetchall()

            conflict_count = 0
            for (
                id_a,
                term,
                def_a,
                doc_a,
                id_b,
                doc_b,
                def_b,
            ) in rows:
                cur.execute(
                    """
                    UPDATE defined_terms_registry
                    SET conflict_flag = TRUE,
                        conflict_description = %s
                    WHERE id = %s
                    """,
                    (
                        f'Conflicting definition in document {doc_b}: "{def_b[:200]}"',
                        id_a,
                    ),
                )
                cur.execute(
                    """
                    UPDATE defined_terms_registry
                    SET conflict_flag = TRUE,
                        conflict_description = %s
                    WHERE id = %s
                    """,
                    (
                        f'Conflicting definition in document {doc_a}: "{def_a[:200]}"',
                        id_b,
                    ),
                )
                conflict_count += 1

            conn.commit()
            logger.info(
                "[resolve_defined_term_conflicts] ✓ Flagged %d conflicts "
                "for workspace %s",
                conflict_count,
                workspace_id,
            )

    except Exception as e:
        conn.rollback()
        logger.error("[resolve_defined_term_conflicts] Failed: %s", e)
        raise self.retry(exc=e)
    finally:
        pg_pool.putconn(conn)


@celery_app.task(
    name="app.tasks.resolve_deadline_conflicts",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def resolve_deadline_conflicts(self, workspace_id: str):
    """
    Workspace-wide conflict sweep for deadlines/obligations.

    Finds obligations across different documents that reference the same
    obligation type + description but have different deadline expressions,
    then flags them with `conflict_flag = TRUE` and resolution_status = CONFLICTED.

    SRS Stage 6.2: Cross-document deadline scan after all documents complete
    the 'deadlines' extraction stage.
    """
    logger.info("[resolve_deadline_conflicts] Sweeping workspace %s", workspace_id)
    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    try:
        with conn.cursor() as cur:
            # Find same obligation_type + similar description, different raw dates
            cur.execute(
                """
                SELECT a.id, a.obligation_description, a.raw_date_expression,
                       b.id, b.raw_date_expression
                FROM deadline_registry a
                JOIN deadline_registry b
                  ON a.workspace_id = b.workspace_id
                 AND a.obligation_type = b.obligation_type
                 AND a.id < b.id
                 AND a.raw_date_expression != b.raw_date_expression
                WHERE a.workspace_id = %s
                  AND a.conflict_flag = FALSE
                  AND b.conflict_flag = FALSE
                """,
                (workspace_id,),
            )
            rows = cur.fetchall()

            conflict_count = 0
            for (
                id_a,
                desc_a,
                date_a,
                id_b,
                date_b,
            ) in rows:
                cur.execute(
                    """
                    UPDATE deadline_registry
                    SET conflict_flag = TRUE,
                        resolution_status = 'CONFLICTED'
                    WHERE id = %s
                    """,
                    (id_a,),
                )
                cur.execute(
                    """
                    UPDATE deadline_registry
                    SET conflict_flag = TRUE,
                        resolution_status = 'CONFLICTED'
                    WHERE id = %s
                    """,
                    (id_b,),
                )
                conflict_count += 1

            conn.commit()
            logger.info(
                "[resolve_deadline_conflicts] ✓ Flagged %d conflicts for workspace %s",
                conflict_count,
                workspace_id,
            )

    except Exception as e:
        conn.rollback()
        logger.error("[resolve_deadline_conflicts] Failed: %s", e)
        raise self.retry(exc=e)
    finally:
        pg_pool.putconn(conn)


# ─────────────────────────────────────────────────────────────────────────────
# Deadline Scanner (Celery Beat — every 15 minutes)
# ─────────────────────────────────────────────────────────────────────────────


def calculate_urgency(days_remaining: int) -> float:
    """
    Pure function: calculate an urgency score from days remaining until
    a deadline.

    SRS FR-DEAD-02 exact thresholds:
      days_remaining <= 0  → OVERDUE (score returned as 1.0, caller also marks OVERDUE)
      days_remaining <= 1  → exactly 0.95
      1 < days_remaining <= 3  → exactly 0.85 (or max of current and 0.85 at update time)
      days_remaining <= 7  → 0.70
      days_remaining <= 14 → 0.50
      days_remaining <= 30 → 0.30
      otherwise            → 0.10
    """
    if days_remaining <= 0:
        return 1.0  # Caller also sets status=OVERDUE
    if days_remaining <= 1:
        return 0.95  # SRS: exactly 0.95 for <=1 day
    if days_remaining <= 3:
        return 0.85  # SRS: exactly 0.85 for 1 < days <= 3
    if days_remaining <= 7:
        return 0.70
    if days_remaining <= 14:
        return 0.50
    if days_remaining <= 30:
        return 0.30
    return 0.10


@celery_app.task(
    name="app.tasks.deadline_scanner",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def deadline_scanner(self):
    """
    Celery Beat task that runs every 15 minutes.

    Scans all ACTIVE deadlines, recalculates urgency_score, marks OVERDUE
    deadlines, triggers admin notifications, and pushes urgency updates to
    linked ACT-path actions.

    NFR-SCALE-01: Runs on dedicated "deadline" queue/worker.
    NFR-REL-02: Acks only after successful Postgres persist.
    """
    from datetime import datetime, timezone

    logger.info("[deadline_scanner] Starting scan...")
    pg_pool = get_pg_pool()
    conn, _ = _get_valid_conn(pg_pool)
    try:
        with conn.cursor() as cur:
            # ── Fetch all ACTIVE deadlines with a resolved date ──
            cur.execute(
                """
                SELECT id, resolved_deadline, urgency_score, obligation_description,
                       workspace_id, org_id
                FROM deadline_registry
                WHERE status = 'ACTIVE'
                  AND resolved_deadline IS NOT NULL
                """
            )
            rows = cur.fetchall()

        # Clear transaction state from read before starting write operations
        conn.rollback()

        now = datetime.now(timezone.utc)
        overdue_ids: list[str] = []
        urgency_updates: list[tuple[float, str]] = []  # (new_score, id)

        for row in rows:
            reg_id, resolved, old_score, desc, ws_id, org_id = row
            days_remaining = (resolved - now).days

            # Calculate urgency score
            new_score = calculate_urgency(days_remaining)
            if days_remaining <= 0:
                overdue_ids.append(reg_id)

            urgency_updates.append((new_score, reg_id))

        # ── Write operations: single cursor block ──
        notified_count = 0
        if urgency_updates:
            with conn.cursor() as cur:
                for score, rid in urgency_updates:
                    cur.execute(
                        "UPDATE deadline_registry SET urgency_score = %s WHERE id::text = %s",
                        (score, str(rid)),
                    )

                # ── Mark overdue deadlines ──
                if overdue_ids:
                    cur.execute(
                        """
                        UPDATE deadline_registry
                        SET status = 'OVERDUE'
                        WHERE id::text = ANY(%s)
                        """,
                        (overdue_ids,),
                    )
                    logger.warning(
                        "[deadline_scanner] Marked %d deadlines as OVERDUE",
                        len(overdue_ids),
                    )

                # ── Push urgency to linked ACT actions (score >= 0.85) ──
                cur.execute(
                    """
                    SELECT a.id, a.workflow_id, a.status, d.id as deadline_id,
                           d.obligation_description, d.urgency_score
                    FROM actions a
                    JOIN deadline_registry d ON a.deadline_id = d.id
                    WHERE d.urgency_score >= 0.85
                      AND a.status IN ('DETECTED', 'CONFIRMED', 'AWAITING_APPROVAL')
                    """
                )
                linked_actions = cur.fetchall()

                notified_count = 0
                for action_row in linked_actions:
                    action_id, wf_id, action_status, dl_id, desc, urgency = action_row
                    cur.execute(
                        "UPDATE actions SET urgency_score = %s, updated_at = NOW() WHERE id = %s",
                        (urgency, action_id),
                    )

                    if action_status == "AWAITING_APPROVAL" and urgency >= 0.85:
                        _notify_approver_urgency(dl_id, desc, urgency)
                        notified_count += 1

        conn.commit()

        logger.info(
            "[deadline_scanner] ✓ Scanned %d deadlines (%d overdue, "
            "%d approver re-notifications)",
            len(rows),
            len(overdue_ids),
            notified_count,
        )

        # ── Trigger admin notifications for overdue (fresh conn, outside transaction) ──
        if overdue_ids:
            _notify_admins_overdue(overdue_ids, pg_pool)

    except Exception as e:
        conn.rollback()
        logger.error("[deadline_scanner] Failed: %s", e)
        raise self.retry(exc=e)
    finally:
        pg_pool.putconn(conn)


@celery_app.task(
    name="app.tasks.stale_task_sweeper",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def stale_task_sweeper(self):
    """
    Celery Beat task that runs every 5 minutes.

    Marks any PROCESSING/PENDING records that have been stuck for too long
    as FAILED so the frontend stops polling and the user sees a real error.

    Thresholds:
      - WorkflowExecution stuck in PROCESSING/PENDING > 10 min → FAILED
      - Document stuck in PROCESSING/PENDING > 15 min → FAILED
      - Goal stuck in PROCESSING > 10 min → FAILED
    """
    from datetime import datetime, timezone

    pg_pool = get_pg_pool()
    conn, _ = _get_valid_conn(pg_pool)
    try:
        with conn.cursor() as cur:
            now = datetime.now(timezone.utc)

            # ── WorkflowExecution ─────────────────────────────────────────
            cur.execute(
                """
                UPDATE workflow_executions
                SET    status = 'FAILED'
                WHERE  status IN ('PROCESSING', 'PENDING', 'CLASSIFYING',
                                  'RETRIEVING', 'EXPANDING', 'REASONING',
                                  'BRIEFING', 'DRAFTING', 'RECOVERING')
                  AND  created_at < NOW() - INTERVAL '10 minutes'
                RETURNING id
                """
            )
            wf_rows = cur.fetchall()

            # ── Goals ─────────────────────────────────────────────────────
            cur.execute(
                """
                UPDATE goals
                SET    status = 'FAILED'
                WHERE  status = 'PROCESSING'
                  AND  created_at < NOW() - INTERVAL '10 minutes'
                RETURNING id
                """
            )
            goal_rows = cur.fetchall()

            # ── Documents ────────────────────────────────────────────────
            cur.execute(
                """
                UPDATE documents
                SET    status = 'FAILED',
                       error_message = 'Swept: ingestion task exceeded time limit'
                WHERE  status IN ('PROCESSING', 'PENDING')
                  AND  upload_date < NOW() - INTERVAL '15 minutes'
                RETURNING id
                """
            )
            doc_rows = cur.fetchall()

        conn.commit()
        logger.info(
            "[stale_task_sweeper] ✓ Swept %d workflows, %d goals, %d documents",
            len(wf_rows),
            len(goal_rows),
            len(doc_rows),
        )
    except Exception as e:
        conn.rollback()
        logger.error("[stale_task_sweeper] Failed: %s", e)
    finally:
        pg_pool.putconn(conn)


def _notify_approver_urgency(
    deadline_id: str,
    obligation_desc: str,
    urgency_score: float,
):
    """
    Send a re-notification to the approver for an ACT-path action whose
    linked deadline urgency has reached 0.85+.

    Independent retry: 3 attempts with 30s/60s/120s exponential backoff.
    """
    delays = [30, 60, 120]
    last_error = None
    for attempt, delay in enumerate(delays):
        try:
            # In production this would dispatch via the Notification Layer
            logger.info(
                "[notify_approver] Urgency %.2f for deadline %s (%s) "
                "— re-notifying approver",
                urgency_score,
                deadline_id,
                obligation_desc[:80],
            )
            return
        except Exception as e:
            last_error = e
            if attempt < len(delays) - 1:
                import time

                time.sleep(delay)
    if last_error:
        logger.error(
            "[notify_approver] Failed after 3 retries for deadline %s: %s",
            deadline_id,
            last_error,
        )


def _notify_admins_overdue(
    overdue_ids: list,
    pg_pool,
):
    """
    Fire-and-forget admin notifications for overdue deadlines.
    Uses its own connection from the pool (does not reuse the scanner's returned conn).
    Each notification uses independent 3-retry backoff.
    """
    notify_conn = pg_pool.getconn()
    try:
        with notify_conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, obligation_description, resolved_deadline, workspace_id
                FROM deadline_registry
                WHERE id = ANY(%s)
                """,
                (overdue_ids,),
            )
            overdue_rows = cur.fetchall()
    except Exception:
        pg_pool.putconn(notify_conn)
        return

    delays = [30, 60, 120]
    for row in overdue_rows:
        dl_id, desc, resolved, ws_id = row
        for attempt, delay in enumerate(delays):
            try:
                logger.warning(
                    "[notify_admin] OVERDUE: %s (%s) was due %s in workspace %s",
                    desc[:80],
                    dl_id,
                    resolved.date(),
                    ws_id,
                )
                break
            except Exception as e:
                if attempt < len(delays) - 1:
                    import time

                    time.sleep(delay)
                else:
                    logger.error(
                        "[notify_admin] Failed after 3 retries for %s: %s",
                        dl_id,
                        e,
                    )
    pg_pool.putconn(notify_conn)


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
        from app.services.ingestion.chunker import ClauseChunker
        from app.services.ingestion.parser import LegalDocumentParser

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
            update_postgres_status_sync(
                document_id_str, "FAILED", error="No text could be extracted."
            )
            return

        update_progress_sync(document_id_str, 20)

        # 2. Chunk
        chunker = ClauseChunker(body_font_size=parser.body_font_size)
        chunks = chunker.build_chunks(raw_blocks)

        if not chunks:
            update_postgres_status_sync(
                document_id_str, "FAILED", error="Chunking produced no results."
            )
            return

        # Add document metadata to chunks
        for chunk in chunks:
            chunk["document_name"] = filename

        update_progress_sync(document_id_str, 40)

        # 3. Parallel Intelligence Pipelines
        from app.worker import get_worker_loop

        stages_complete = asyncio.run_coroutine_threadsafe(
            run_intelligence_pipeline_async(
                document_id, workspace_id, org_id, filename, chunks
            ),
            get_worker_loop(),
        ).result()

        update_progress_sync(document_id_str, 90)

        # 4. Mark Ready
        update_postgres_status_sync(
            document_id_str, "READY", stages_complete=stages_complete
        )
        update_progress_sync(document_id_str, 100)
        logger.info(f"[{document_id_str}] Document Processing Complete")

    except LeaseExhaustedError as e:
        logger.warning(
            f"[{document_id_str}] Lease Exhausted, re-raising for Celery retry..."
        )
        raise
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
def process_digital_pdf(
    self,
    document_id: str,
    workspace_id: str,
    org_id: str,
    filename: str,
    blob_name: str,
):
    import os

    from app.services.object_storage import delete_file_from_gcs, download_file_from_gcs

    local_temp_path = f"/tmp/{uuid.uuid4().hex}_{filename}"
    try:
        download_file_from_gcs(blob_name, local_temp_path)
        _process_document_core(
            document_id, workspace_id, org_id, filename, local_temp_path
        )
        delete_file_from_gcs(blob_name)
    except LeaseExhaustedError as exc:
        raise self.retry(exc=exc)
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
def process_scanned_pdf(
    self,
    document_id: str,
    workspace_id: str,
    org_id: str,
    filename: str,
    blob_name: str,
):
    import os

    from app.services.object_storage import delete_file_from_gcs, download_file_from_gcs

    local_temp_path = f"/tmp/{uuid.uuid4().hex}_{filename}"
    try:
        download_file_from_gcs(blob_name, local_temp_path)

        if not GEMINI_API_KEY:
            raise RuntimeError("Scanned PDF OCR requires GEMINI_API_KEY.")

        from app.services.ocr_gemini import ocr_pdf_path_to_markdown_pages

        pages_data = ocr_pdf_path_to_markdown_pages(
            local_temp_path, api_key=GEMINI_API_KEY, model=GEMINI_MODEL
        )

        pages_data = [
            {"page": int(p.get("page") or 1), "text": (p.get("text") or "").strip()}
            for p in pages_data
            if (p.get("text") or "").strip()
        ]

        if not pages_data:
            raise RuntimeError("Gemini OCR returned empty text.")

        _process_document_core(
            document_id,
            workspace_id,
            org_id,
            filename,
            local_temp_path,
            pages_data_override=pages_data,
        )
        delete_file_from_gcs(blob_name)
    except LeaseExhaustedError as exc:
        raise self.retry(exc=exc)
    except Exception as exc:
        safe_exc = RuntimeError(f"Task failed: {exc}")
        raise self.retry(exc=safe_exc)
    finally:
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)


def _reset_async_engine() -> None:
    """Reset the async engine cache so a fresh engine + pool is created.

    Called before each ``asyncio.run()`` to prevent stale asyncpg connections
    tied to a previous event loop from being reused on the new loop.
    """
    try:
        from app.database import _get_engine, _reset_engine

        _reset_engine()
    except Exception:
        logger.debug("Async engine reset skipped")


@celery_app.task(
    name="app.tasks.process_workflow",
    bind=True,
    max_retries=0,
    queue="default",
    acks_late=True,
    soft_time_limit=300,
    time_limit=360,
)
def process_workflow(
    self,
    workflow_id: str,
    session_file_ids: list | None = None,
    preclassified_intent: str | None = None,
    intent_confidence: float = 0.0,
):
    """Direct node calls. Uses asyncio.run() with sync DB."""
    import asyncio
    import uuid

    import sentry_sdk

    from app.services.agent.agent_state import CURRENT_GRAPH_VERSION, PointerOnlyState
    from app.services.agent.nodes import (
        ambiguity_gate_node,
        contradiction_node,
        decision_brief_node,
        defined_terms_node,
        detect_node,
        draft_node,
        escalation_node,
        export_node,
        findings_node,
        graph_expansion_node,
        intent_node,
        result_node,
        retrieval_node,
        synthesis_node,
    )
    from app.tasks import _get_valid_conn, get_pg_pool

    sentry_sdk.set_tag("workflow_id", workflow_id)
    logger.info("[%s] process_workflow: starting", workflow_id)

    async def _run():
        pg_pool = get_pg_pool()
        conn, _ = _get_valid_conn(pg_pool)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, workspace_id, org_id, status, goal_id FROM workflow_executions WHERE id = %s",
                    (workflow_id,),
                )
                wf_row = cur.fetchone()
                if not wf_row:
                    logger.error("[%s] Workflow not found", workflow_id)
                    return {"status": "not_found"}
                wf_id, ws_id, org_id_str, wf_status, goal_id = wf_row
                cur.execute("SELECT goal_text FROM goals WHERE id = %s", (goal_id,))
                goal_row = cur.fetchone()
                goal_text = goal_row[0] if goal_row else ""
        finally:
            pg_pool.putconn(conn)

        state = PointerOnlyState(
            graph_version=CURRENT_GRAPH_VERSION,
            workflow_id=workflow_id,
            workspace_id=str(ws_id),
            org_id=str(org_id_str),
            document_id="",
            primary_intent="ANALYZE",
            intent_confidence=0.0,
            intent_confirmed_by_human=False,
            goal_text=goal_text,
            context_text=None,
            plan_id=None,
            current_task_index=0,
            total_tasks=0,
            status=wf_status,
            findings_summary="",
            action_count=0,
            session_file_ids=session_file_ids or [],
            messages=[],
            error_context=None,
            retry_count=0,
        )

        if wf_status == "AWAITING_BRIEF_CONFIRMATION":
            logger.info("[%s] Brief confirmed, drafting...", workflow_id)
            draft_result = await draft_node(state)
            if draft_result.get("status") == "FAILED":
                return {"status": "FAILED", "reason": draft_result.get("error_context")}
            state.update(draft_result)
            return {"status": "AWAITING_APPROVAL"}

        if wf_status == "AWAITING_APPROVAL":
            logger.info("[%s] Draft approved, exporting...", workflow_id)
            export_result = await export_node(state)
            state.update(export_result)
            return {"status": export_result.get("status", "COMPLETED")}

        if wf_status == "REVISING":
            # Lawyer rejected the draft — re-run draft_node to generate a
            # revised draft. draft_node reads the brief payload and the
            # rejection reason from the Action row's revision_count.
            logger.info("[%s] Revision requested, regenerating draft...", workflow_id)
            from app.services.agent.nodes import _set_wf_status
            from app.models import WorkflowStatus as _WfStatus
            await _set_wf_status(workflow_id, _WfStatus.DRAFTING)
            draft_result = await draft_node(state)
            if draft_result.get("status") == "FAILED":
                return {"status": "FAILED", "reason": draft_result.get("error_context")}
            state.update(draft_result)
            return {"status": "AWAITING_APPROVAL"}


        if preclassified_intent:
            intent = preclassified_intent
            state["primary_intent"] = intent
            state["intent_confidence"] = intent_confidence
            logger.info(
                "[%s] Using pre-classified intent: %s (conf=%.2f)",
                workflow_id,
                intent,
                intent_confidence,
            )
        else:
            state.update(await intent_node(state))
            intent = state["primary_intent"]
            logger.info(
                "[%s] Intent: %s (conf=%.2f)",
                workflow_id,
                intent,
                state.get("intent_confidence", 0),
            )

        if state.get("intent_confidence", 0) < 0.80 and not state.get(
            "intent_confirmed_by_human", False
        ):
            state.update(await ambiguity_gate_node(state))
            if state.get("status") == "AWAITING_INTENT_CONFIRMATION":
                logger.info("[%s] Paused at ambiguity gate", workflow_id)
                return {"status": "AWAITING_INTENT_CONFIRMATION"}

        if intent == "ANALYZE":
            state.update(await retrieval_node(state))
            if state.get("retrieval_aborted"):
                return {"status": "COMPLETED"}
            state.update(await synthesis_node(state))
            return {"status": "COMPLETED"}

        elif intent == "REASON":
            state.update(await retrieval_node(state))
            if state.get("retrieval_aborted"):
                return {"status": "COMPLETED"}
            state.update(await graph_expansion_node(state))
            state.update(await defined_terms_node(state))
            state.update(await contradiction_node(state))
            state.update(await findings_node(state))
            state.update(await escalation_node(state))
            state.update(await result_node(state))
            return {"status": "COMPLETED"}

        elif intent == "ACT":
            state.update(await retrieval_node(state))
            if state.get("retrieval_aborted"):
                logger.info(
                    "[%s] ACT: low-confidence chunks, proceeding anyway", workflow_id
                )
            state.update(await detect_node(state))
            state.update(await decision_brief_node(state))
            logger.info("[%s] ACT: brief generated, awaiting confirmation", workflow_id)
            return {"status": "AWAITING_BRIEF_CONFIRMATION"}

        else:
            logger.warning("[%s] Unknown intent: %s", workflow_id, intent)
            return {"status": "FAILED"}

    _reset_async_engine()

    async def _safe_run():
        """Top-level safety net: any uncaught exception marks the workflow FAILED."""
        try:
            return await _run()
        except Exception as top_err:
            logger.exception(
                "[%s] process_workflow: UNHANDLED exception — marking FAILED: %s",
                workflow_id,
                top_err,
            )
            # Best-effort DB update so the workflow doesn't stay stuck forever.
            # Uses error_context column (Text) on workflow_executions.
            try:
                pg_pool = get_pg_pool()
                conn, _ = _get_valid_conn(pg_pool)
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE workflow_executions
                               SET status       = 'FAILED',
                                   error_context = %s
                             WHERE id = %s
                            """,
                            (
                                f"Unhandled worker error: {str(top_err)[:400]}",
                                workflow_id,
                            ),
                        )
                    conn.commit()
                finally:
                    pg_pool.putconn(conn)
            except Exception as db_err:
                logger.error(
                    "[%s] process_workflow: could not write FAILED status to DB: %s",
                    workflow_id,
                    db_err,
                )
            return {"status": "FAILED", "error_context": str(top_err)[:400]}

    result = asyncio.run(_safe_run())
    logger.info("[%s] process_workflow: done -> %s", workflow_id, result)

    return result


@celery_app.task(name="app.tasks.cleanup_stale_data")
def cleanup_stale_data():
    """
    Periodic maintenance task to purge expired invites and stale temporary files.
    """
    import time
    from datetime import datetime, timezone

    pg_pool = get_pg_pool()
    conn = pg_pool.getconn()
    deleted_invites = 0
    deleted_tmp = 0

    try:
        with conn.cursor() as cur:
            # 1. Purge expired invites that were never accepted
            cur.execute(
                "DELETE FROM organization_invites WHERE expires_at < %s AND is_accepted = FALSE",
                (datetime.now(timezone.utc),),
            )
            deleted_invites = cur.rowcount
        conn.commit()

        # 2. Sweep /tmp for processing remnants older than 2 hours
        # 2. Purge stale temporary files (/tmp)
        tmp_dir = "/tmp"
        now = time.time()
        for filename in os.listdir(tmp_dir):
            # Target files created by our PDF processing tasks
            if (
                filename.startswith("temp_")
                or "_digital_" in filename
                or "_scanned_" in filename
                or filename.startswith("lex_upload_")
            ):
                file_path = os.path.join(tmp_dir, filename)
                try:
                    if (
                        os.path.isfile(file_path)
                        and now - os.path.getmtime(file_path) > 7200
                    ):
                        os.remove(file_path)
                        deleted_tmp += 1
                except Exception:
                    continue

        logger.info(
            "Maintenance Task: Purged %d expired invites and %d stale temp files.",
            deleted_invites,
            deleted_tmp,
        )

    except Exception as e:
        conn.rollback()
        logger.error("Maintenance Task Failed: %s", e)
    finally:
        pg_pool.putconn(conn)


# Register test brief task
# from app.test_brief_task import test_decision_brief  # noqa: F401
