# app/services/audit/events.py
#
# Semantic event emitters.
#
# Instead of sprinkling raw AuditLogger.audit(...) everywhere, call these:
#   from app.services.audit.events import emit
#   await emit.workflow_started(workflow_id="...", ...)
#
# Every emitter enforces a consistent schema, correlation context,
# and correct collection (audit vs debug).

from app.services.audit.logger import AuditLogger
from app.services.audit.schemas import (
    AuditCategory,
    AuditEvent,
    CorrelationContext,
    LogLevel,
)


class _Emitter:
    """Namespace for semantic event emitters. Grouped by domain."""

    # ── Workflow lifecycle ──────────────────────────────────────────────

    @staticmethod
    def workflow_started(
        workflow_id: str,
        intent: str,
        org_id: str | None = None,
        user_id: str | None = None,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.INFO,
                category=AuditCategory.WORKFLOW_LIFECYCLE,
                action="workflow.started",
                correlation=correlation or CorrelationContext(workflow_id=workflow_id),
                message=f"Workflow started: intent={intent}",
                metadata={"intent": intent, "org_id": str(org_id) if org_id else ""},
            )
        )

    @staticmethod
    def workflow_completed(
        workflow_id: str,
        duration_ms: float,
        intent: str | None = None,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.INFO,
                category=AuditCategory.WORKFLOW_LIFECYCLE,
                action="workflow.completed",
                correlation=correlation or CorrelationContext(workflow_id=workflow_id),
                message=f"Workflow completed ({duration_ms:.0f}ms)",
                duration_ms=duration_ms,
                metadata={"intent": intent or ""},
            )
        )

    @staticmethod
    def workflow_failed(
        workflow_id: str,
        error: str,
        duration_ms: float | None = None,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.ERROR,
                category=AuditCategory.WORKFLOW_LIFECYCLE,
                action="workflow.failed",
                correlation=correlation or CorrelationContext(workflow_id=workflow_id),
                message=f"Workflow failed: {error}",
                duration_ms=duration_ms,
                error=error,
            )
        )

    # ── Task lifecycle ──────────────────────────────────────────────────

    @staticmethod
    def task_dispatched(
        task_name: str,
        task_id: str,
        workflow_id: str | None = None,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.INFO,
                category=AuditCategory.TASK_LIFECYCLE,
                action="task.dispatched",
                correlation=correlation or CorrelationContext(task_id=task_id),
                message=f"Task dispatched: {task_name}",
                metadata={"task_name": task_name, "workflow_id": workflow_id or ""},
            )
        )

    @staticmethod
    def task_succeeded(
        task_name: str,
        task_id: str,
        duration_ms: float,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.INFO,
                category=AuditCategory.TASK_LIFECYCLE,
                action="task.succeeded",
                correlation=correlation or CorrelationContext(task_id=task_id),
                message=f"Task succeeded: {task_name} ({duration_ms:.0f}ms)",
                duration_ms=duration_ms,
                metadata={"task_name": task_name},
            )
        )

    @staticmethod
    def task_failed(
        task_name: str,
        task_id: str,
        error: str,
        duration_ms: float | None = None,
        retry_count: int = 0,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.ERROR,
                category=AuditCategory.TASK_LIFECYCLE,
                action="task.failed",
                correlation=correlation or CorrelationContext(task_id=task_id),
                message=f"Task failed: {task_name} — {error}",
                duration_ms=duration_ms,
                error=error,
                metadata={"task_name": task_name, "retry_count": retry_count},
            )
        )

    @staticmethod
    def task_retried(
        task_name: str,
        task_id: str,
        attempt: int,
        error: str,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.WARNING,
                category=AuditCategory.TASK_LIFECYCLE,
                action="task.retried",
                correlation=correlation or CorrelationContext(task_id=task_id),
                message=f"Task retry {attempt}: {task_name}",
                error=error,
                metadata={"task_name": task_name, "attempt": attempt},
            )
        )

    # ── Ingestion ───────────────────────────────────────────────────────

    @staticmethod
    def ingestion_started(
        document_id: str,
        filename: str,
        org_id: str | None = None,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.INFO,
                category=AuditCategory.INGESTION,
                action="ingestion.started",
                correlation=correlation or CorrelationContext(org_id=org_id),
                message=f"Ingestion started: {filename}",
                metadata={"document_id": document_id, "filename": filename},
            )
        )

    @staticmethod
    def ingestion_completed(
        document_id: str,
        filename: str,
        duration_ms: float,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.INFO,
                category=AuditCategory.INGESTION,
                action="ingestion.completed",
                correlation=correlation or CorrelationContext(),
                message=f"Ingestion completed: {filename} ({duration_ms:.0f}ms)",
                metadata={"document_id": document_id, "filename": filename},
                duration_ms=duration_ms,
            )
        )

    @staticmethod
    def ingestion_failed(
        document_id: str,
        filename: str,
        error: str,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.ERROR,
                category=AuditCategory.INGESTION,
                action="ingestion.failed",
                correlation=correlation or CorrelationContext(),
                message=f"Ingestion failed: {filename} — {error}",
                metadata={"document_id": document_id, "filename": filename},
                error=error,
            )
        )

    # ── LLM failures ────────────────────────────────────────────────────

    @staticmethod
    def llm_failure(
        model: str,
        error: str,
        workflow_id: str | None = None,
        duration_ms: float | None = None,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.ERROR,
                category=AuditCategory.LLM_FAILURE,
                action="llm.failure",
                correlation=correlation or CorrelationContext(workflow_id=workflow_id),
                message=f"LLM failure: {model} — {error}",
                metadata={"model": model},
                duration_ms=duration_ms,
                error=error,
            )
        )

    # ── Errors (unhandled exceptions) ───────────────────────────────────

    @staticmethod
    def unhandled_error(
        action: str,
        error: str,
        stack_trace: str,
        correlation: CorrelationContext | None = None,
    ) -> None:
        AuditLogger.audit(
            AuditEvent(
                level=LogLevel.CRITICAL,
                category=AuditCategory.ERROR,
                action=action,
                correlation=correlation or CorrelationContext(),
                message=f"Unhandled error: {error}",
                error=error,
                stack_trace=stack_trace,
            )
        )


# Singleton — import this as `from app.services.audit.events import emit`
emit = _Emitter()
