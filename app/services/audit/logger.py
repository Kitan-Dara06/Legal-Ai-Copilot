# app/services/audit/logger.py
# Thread-safe batch writer with dedicated daemon thread + persistent event loop.

import asyncio
import logging
import os
import queue
import threading
import traceback as tb

from app.services.audit.client import get_audit_db
from app.services.audit.schemas import (
    AuditCategory,
    AuditEvent,
    CorrelationContext,
    DebugCategory,
    DebugTrace,
    LogLevel,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = int(os.getenv("AUDIT_BATCH_SIZE", "100"))
FLUSH_INTERVAL = float(os.getenv("AUDIT_FLUSH_INTERVAL", "0.5"))

# Thread-safe queue for cross-thread communication
_q: queue.Queue = queue.Queue(maxsize=5000)

# The consumer's persistent event loop — set once, never closed
_loop: asyncio.AbstractEventLoop | None = None


class AuditLogger:
    @staticmethod
    def audit(event):
        try:
            _q.put_nowait(("audit", event.model_dump()))
        except queue.Full:
            logger.warning("Audit queue full - dropping event: %s", event.action)

    @staticmethod
    def debug(trace):
        try:
            _q.put_nowait(("debug", trace.model_dump()))
        except queue.Full:
            pass

    @staticmethod
    def error(action, message, correlation=None, duration_ms=None, metadata=None):
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.ERROR,
                category=AuditCategory.ERROR,
                action=action,
                correlation=correlation or CorrelationContext(),
                message=message,
                duration_ms=duration_ms,
                metadata=metadata or {},
                error=message,
                stack_trace=tb.format_exc(),
            )
        )

    @staticmethod
    def llm_call(
        model, tokens_in, tokens_out, duration_ms, correlation=None, error=None
    ):
        AuditLogger.debug(
            DebugTrace(
                category=DebugCategory.LLM_CALL,
                correlation=correlation or CorrelationContext(),
                message=f"LLM call: {model} ({tokens_in}->{tokens_out} tok, {duration_ms:.0f}ms)",
                duration_ms=duration_ms,
                llm_model=model,
                llm_tokens_in=tokens_in,
                llm_tokens_out=tokens_out,
                llm_duration_ms=duration_ms,
                error=error,
                level=LogLevel.ERROR if error else LogLevel.DEBUG,
            )
        )

    @staticmethod
    def node_enter(node_name, correlation=None):
        AuditLogger.debug(
            DebugTrace(
                category=DebugCategory.NODE_ENTER,
                correlation=correlation or CorrelationContext(),
                node=node_name,
                message=f"Enter: {node_name}",
            )
        )

    @staticmethod
    def node_exit(node_name, duration_ms, correlation=None, error=None):
        level = LogLevel.ERROR if error else LogLevel.DEBUG
        AuditLogger.debug(
            DebugTrace(
                category=DebugCategory.NODE_EXIT,
                correlation=correlation or CorrelationContext(),
                node=node_name,
                message=f"Exit: {node_name} ({duration_ms:.0f}ms)",
                duration_ms=duration_ms,
                error=error,
                level=level,
            )
        )


# ── Background consumer (runs on persistent event loop in daemon thread) ──


async def _flush_batch(entries):
    db = get_audit_db()
    if db is None:
        return
    audit_docs = []
    debug_docs = []
    for kind, doc in entries:
        if kind == "audit":
            audit_docs.append(doc)
        else:
            debug_docs.append(doc)
    if not audit_docs and not debug_docs:
        return
    try:
        if audit_docs:
            await db.audit_events.insert_many(audit_docs, ordered=False)
        if debug_docs:
            await db.debug_traces.insert_many(debug_docs, ordered=False)
    except Exception as e:
        logger.warning("Mongo batch insert failed (%d docs): %s", len(entries), e)


async def _consumer_loop():
    batch = []
    while True:
        # Drain queue entries with timeout
        try:
            item = _q.get_nowait()
            batch.append(item)
        except queue.Empty:
            await asyncio.sleep(FLUSH_INTERVAL)

        while len(batch) < BATCH_SIZE:
            try:
                batch.append(_q.get_nowait())
            except queue.Empty:
                break

        if batch:
            await _flush_batch(batch)
            batch.clear()


def _run_consumer():
    """Target for daemon thread. Connects Mongo, then runs consumer forever."""
    global _loop
    from app.services.audit.client import init_audit_db

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    try:
        _loop.run_until_complete(init_audit_db())
        logger.info("MongoDB audit init OK (consumer thread).")
    except Exception as e:
        logger.warning("MongoDB audit init failed (consumer thread): %s", e)
        return

    _loop.create_task(_consumer_loop(), name="audit-consumer")
    logger.info(
        "Audit consumer started (batch=%d, interval=%.1fs)", BATCH_SIZE, FLUSH_INTERVAL
    )
    _loop.run_forever()


def start_consumer():
    """Start the background consumer daemon thread."""
    t = threading.Thread(
        target=_run_consumer, daemon=True, name="audit-consumer-thread"
    )
    t.start()
