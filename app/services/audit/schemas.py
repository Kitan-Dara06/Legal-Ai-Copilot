# app/services/audit/schemas.py
#
# Log entry shapes — two tiers:
#   audit_events  (important business events, 90-day retention)
#   debug_traces  (high-volume diagnostic spans, 7-day retention)

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ── Categories ──────────────────────────────────────────────────────────────


class AuditCategory(str, Enum):
    """Business-level events — stored in audit_events collection."""

    WORKFLOW_LIFECYCLE = "workflow_lifecycle"  # started, completed, failed
    TASK_LIFECYCLE = (
        "task_lifecycle"  # dispatched, received, succeeded, failed, retried
    )
    INGESTION = "ingestion"  # document uploaded, processed, failed
    AUTH = "auth"  # login, token refresh, API key used
    APPROVAL = "approval"  # approved, rejected, expired
    DB_QUERY = "db_query"  # database connection lifecycle (pool, setup)
    ERROR = "error"  # unhandled exception with stack trace
    LLM_FAILURE = "llm_failure"  # LLM API error (not normal responses)


class DebugCategory(str, Enum):
    """High-volume diagnostic events — stored in debug_traces collection."""

    NODE_ENTER = "node_enter"  # entered a LangGraph node
    NODE_EXIT = "node_exit"  # exited a LangGraph node (with duration)
    LLM_CALL = "llm_call"  # LLM API call (token counts, duration, model)
    DB_QUERY = "db_query"  # slow query (>200ms)
    RETRY = "retry"  # task retry with attempt number
    EMBEDDING = "embedding"  # embedding generation (model, dims, duration)
    SEARCH = "search"  # Qdrant search (hits, duration)


# ── Severity levels ─────────────────────────────────────────────────────────


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ── Shared correlation context ──────────────────────────────────────────────


class CorrelationContext(BaseModel):
    """Propagated automatically across request → task → node boundaries."""

    request_id: Optional[str] = None  # FastAPI request uuid
    trace_id: Optional[str] = None  # LangGraph trace id
    workflow_id: Optional[str] = None
    task_id: Optional[str] = None  # Celery task id
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    workspace_id: Optional[str] = None


# ── Audit event (business-level, 90-day TTL) ───────────────────────────────


class AuditEvent(BaseModel):
    """Important business event. Stored in audit_events collection."""

    schema_version: int = 2
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level: LogLevel = LogLevel.INFO
    category: AuditCategory
    action: str  # e.g. "workflow.started", "document.uploaded", "task.failed"

    correlation: CorrelationContext = Field(default_factory=CorrelationContext)

    message: str  # human-readable summary
    duration_ms: Optional[float] = None

    # NO full LLM inputs/outputs here — only metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    stack_trace: Optional[str] = None  # only on ERROR / CRITICAL


# ── Debug trace (high-volume, 7-day TTL) ────────────────────────────────────


class DebugTrace(BaseModel):
    """Diagnostic trace. Stored in debug_traces collection. 7-day retention."""

    schema_version: int = 2
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    level: LogLevel = LogLevel.DEBUG
    category: DebugCategory

    correlation: CorrelationContext = Field(default_factory=CorrelationContext)

    node: Optional[str] = None  # LangGraph node name
    message: str  # short description
    duration_ms: Optional[float] = None

    # Safe metadata — NEVER raw PII or full document text
    metadata: dict[str, Any] = Field(default_factory=dict)

    # LLM call details (safe fields only — no prompt/response content)
    llm_model: Optional[str] = None
    llm_tokens_in: Optional[int] = None
    llm_tokens_out: Optional[int] = None
    llm_duration_ms: Optional[float] = None

    # Error context
    error: Optional[str] = None
