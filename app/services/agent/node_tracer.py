# app/services/agent/node_tracer.py
#
# Tracing decorator for LangGraph nodes.
# Creates a Sentry span + structured log entry for every node invocation,
# so the Sentry Performance view shows the full workflow breakdown:
#
#   process_workflow (transaction)
#   ├── intent_node (span)
#   ├── retrieval_node (span)
#   ├── draft_node (span)
#   ├── qa_node (span)
#   └── plan_node (span)

import functools
import logging
import time

import sentry_sdk

logger = logging.getLogger(__name__)


def traced_node(func):
    """
    Decorator that wraps a LangGraph async node function with:
      - A Sentry span (visible in Sentry Performance waterfall)
      - Structured log INFO at entry / exit with duration
      - Tags: workflow_id, node name, intent (if set)

    Usage:
        @traced_node
        async def intent_node(state: PointerOnlyState) -> Dict[str, Any]:
            ...
    """

    @functools.wraps(func)
    async def wrapper(state, *args, **kwargs):
        workflow_id = state.get("workflow_id", "unknown")
        node_name = func.__name__

        logger.info("[%s] %s: enter", workflow_id, node_name)

        # Build correlation context for MongoDB audit
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import CorrelationContext

            correlation = CorrelationContext(
                workflow_id=workflow_id,
                trace_id=workflow_id,  # reuse workflow_id as trace_id for LangGraph
            )
            AuditLogger.node_enter(node_name, correlation=correlation)
        except Exception:
            pass  # audit failure is non-fatal

        with sentry_sdk.start_span(
            op="langgraph_node",
            description=node_name,
        ) as span:
            span.set_tag("workflow_id", workflow_id)
            span.set_tag("node", node_name)

            t0 = time.monotonic()
            try:
                result = await func(state, *args, **kwargs)

                elapsed = time.monotonic() - t0
                span.set_tag("duration_ms", int(elapsed * 1000))
                # Attach intent tag if the node has set it
                intent = state.get("primary_intent", "")
                if intent:
                    span.set_tag("intent", intent)

                logger.info(
                    "[%s] %s: done (%.2fs)",
                    workflow_id,
                    node_name,
                    elapsed,
                )
                return result

            except Exception as e:
                elapsed = time.monotonic() - t0
                span.set_tag("duration_ms", int(elapsed * 1000))
                span.set_status("internal_error")

                logger.error(
                    "[%s] %s: FAILED after %.2fs — %s: %s",
                    workflow_id,
                    node_name,
                    elapsed,
                    type(e).__name__,
                    e,
                )

                # Log node failure to MongoDB
                try:
                    AuditLogger.node_exit(
                        node_name,
                        duration_ms=elapsed * 1000,
                        correlation=correlation,
                        error=str(e),
                    )
                except Exception:
                    pass

                raise

        # Log node success to MongoDB
        try:
            AuditLogger.node_exit(
                node_name,
                duration_ms=(time.monotonic() - t0) * 1000,
                correlation=correlation,
            )
        except Exception:
            pass

    return wrapper
