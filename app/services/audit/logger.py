# app/services/audit/logger.py
#
# Background batch writer for audit logs.
#
# Instead of writing to Mongo on every log call (which adds latency), we:
#   1. Push log entries into an asyncio.Queue
#   2. A background consumer flushes batches every 0.5s or every 100 entries
#   3. This keeps logging off the hot path — NEVER blocks production traffic
#
# Usage:
#   from app.services.audit.logger import AuditLogger
#   await AuditLogger.audit(AuditEvent(...))
#   await AuditLogger.debug(DebugTrace(...))

import asyncio
import logging
import os
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

# ── Configuration ───────────────────────────────────────────────────────────

BATCH_SIZE = int(os.getenv("AUDIT_BATCH_SIZE", "100"))  # max docs per batch write
FLUSH_INTERVAL = float(os.getenv("AUDIT_FLUSH_INTERVAL", "0.5"))  # seconds

# ── In-memory queue ─────────────────────────────────────────────────────────

_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
_consumer_task: asyncio.Task | None = None


# ── Public API ──────────────────────────────────────────────────────────────


class AuditLogger:
    """Fire-and-forget audit/debug logging. Never awaits Mongo directly."""

    @staticmethod
    def audit(event: AuditEvent) -> None:
        """Enqueue a business audit event. Returns immediately, never blocks."""
        try:
            _queue.put_nowait(("audit", event.model_dump()))
        except asyncio.QueueFull:
            logger.warning("Audit queue full — dropping event: %s", event.action)

    @staticmethod
    def debug(trace: DebugTrace) -> None:
        """Enqueue a debug trace. Returns immediately, never blocks."""
        try:
            _queue.put_nowait(("debug", trace.model_dump()))
        except asyncio.QueueFull:
            pass  # silently drop debug traces when backlogged

    # ── Semantic helpers for common patterns ─────────────────────────────

    @staticmethod
    def error(
        action: str,
        message: str,
        correlation: CorrelationContext | None = None,
        duration_ms: float | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Quick helper for error-level audit events."""
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
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_ms: float,
        correlation: CorrelationContext | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Log an LLM API call — token counts + duration, NO prompt/response."""
        AuditLogger.debug(
            DebugTrace(
                category=DebugCategory.LLM_CALL,
                correlation=correlation or CorrelationContext(),
                message=f"LLM call: {model} ({tokens_in}→{tokens_out} tok, {duration_ms:.0f}ms)",
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
    def node_enter(
        node_name: str,
        correlation: CorrelationContext | None = None,
    ) -> None:
        """Log LangGraph node entry (high-volume debug trace)."""
        AuditLogger.debug(
            DebugTrace(
                category=DebugCategory.NODE_ENTER,
                correlation=correlation or CorrelationContext(),
                node=node_name,
                message=f"Enter: {node_name}",
            )
        )

    @staticmethod
    def node_exit(
        node_name: str,
        duration_ms: float,
        correlation: CorrelationContext | None = None,
        error: str | None = None,
    ) -> None:
        """Log LangGraph node exit with duration."""
        category = DebugCategory.NODE_EXIT
        level = LogLevel.ERROR if error else LogLevel.DEBUG
        AuditLogger.debug(
            DebugTrace(
                category=category,
                correlation=correlation or CorrelationContext(),
                node=node_name,
                message=f"Exit: {node_name} ({duration_ms:.0f}ms)",
                duration_ms=duration_ms,
                error=error,
                level=level,
            )
        )


# ── Background consumer ─────────────────────────────────────────────────────


async def _flush_batch(entries: list[tuple[str, dict]]) -> None:
    """Write a batch of entries to MongoDB. Silent on failure — never crashes."""
    db = get_audit_db()
    if db is None:
        return  # Mongo not available

    audit_docs = []
    debug_docs = []
    for kind, doc in entries:
        if kind == "audit":
            audit_docs.append(doc)
        else:
            debug_docs.append(doc)

    try:
        if audit_docs:
            await db.audit_events.insert_many(audit_docs, ordered=False)
        if debug_docs:
            await db.debug_traces.insert_many(debug_docs, ordered=False)
    except Exception as e:
        logger.warning("Mongo batch insert failed (%d docs): %s", len(entries), e)


async def _consumer_loop() -> None:
    """Background task: drain queue and flush batches."""
    batch: list[tuple[str, dict]] = []

    while True:
        try:
            # Wait for first item with timeout
            item = await asyncio.wait_for(_queue.get(), timeout=FLUSH_INTERVAL)
            batch.append(item)
        except asyncio.TimeoutError:
            pass  # timeout — flush whatever we have

        # Drain any additional items available immediately
        while len(batch) < BATCH_SIZE and not _queue.empty():
            try:
                batch.append(_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if batch:
            await _flush_batch(batch)
            batch.clear()


def start_consumer() -> None:
    """Start the background consumer task. Call once at startup."""
    global _consumer_task
    if _consumer_task is not None and not _consumer_task.done():
        return  # already running

    _consumer_task = asyncio.create_task(_consumer_loop(), name="audit-consumer")
    logger.info(
        "Audit consumer started (batch=%d, interval=%.1fs)",
        BATCH_SIZE,
        FLUSH_INTERVAL,
    )


async def stop_consumer() -> None:
    """Flush remaining entries and stop the consumer. Call at shutdown."""
    global _consumer_task
    if _consumer_task is None:
        return

    # Flush whatever is left in the queue
    remaining: list[tuple[str, dict]] = []
    while not _queue.empty():
        try:
            remaining.append(_queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    if remaining:
        await _flush_batch(remaining)

    _consumer_task.cancel()
    try:
        await _consumer_task
    except asyncio.CancelledError:
        pass
    _consumer_task = None
    logger.info("Audit consumer stopped (flushed %d remaining entries)", len(remaining))
