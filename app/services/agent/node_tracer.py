# app/services/agent/node_tracer.py
#
# LangGraph Node Tracer — uses LangChain callbacks to trace node execution.
# This is the ONLY reliable way to hook into LangGraph node execution,
# as function decorators are bypassed by LangGraph's internal machinery.

import logging
import os
import time

from langchain_core.callbacks import AsyncCallbackHandler

logger = logging.getLogger(__name__)


class NodeTracer(AsyncCallbackHandler):
    """
    LangChain callback handler that captures node start/end with timing.
    Writes directly to MongoDB (bypasses queue/consumer for reliability).

    Usage:
        tracer = NodeTracer()
        app = graph.compile(checkpointer=checkpointer)
        result = await app.ainvoke(input, {"callbacks": [tracer]})
    """

    def __init__(self):
        self._starts: dict = {}

    async def on_chain_start(self, serialized, inputs, **kwargs):
        name = kwargs.get("metadata", {}).get("langraph_node") or kwargs.get(
            "metadata", {}
        ).get("langgraph_node")
        if not name:
            return
        run_id = kwargs.get("run_id")
        if not run_id:
            return
        self._starts[run_id] = (name, time.monotonic())
        _log_debug(name, "enter")

    async def on_chain_end(self, outputs, **kwargs):
        run_id = kwargs.get("run_id")
        if not run_id or run_id not in self._starts:
            return
        name, t0 = self._starts.pop(run_id)
        elapsed = (time.monotonic() - t0) * 1000
        _log_debug(name, "exit", elapsed)

    async def on_chain_error(self, error, **kwargs):
        run_id = kwargs.get("run_id")
        if not run_id or run_id not in self._starts:
            return
        name, t0 = self._starts.pop(run_id)
        elapsed = (time.monotonic() - t0) * 1000
        _log_debug(name, "exit", elapsed, error=str(error))

# kept for backward compat — nodes.py imports this
traced_node = lambda func: func


def _log_debug(node_name: str, action: str, duration_ms: float = 0, error: str = ""):
    """Log to stdout (visible in worker logs) + MongoDB."""
    message = f"{'Enter' if action == 'enter' else 'Exit'}: {node_name}"
    if duration_ms:
        message += f" ({duration_ms:.0f}ms)"
    if error:
        message += f" FAILED: {error}"
    logger.info("[tracer] %s", message)

    # Direct MongoDB write
    try:
        import asyncio

        from motor.motor_asyncio import AsyncIOMotorClient

        uri = os.environ.get("MONGODB_URI", "")
        if not uri:
            return
        mc = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
        db = mc["cluster0"]
        entry = {
            "schema_version": 2,
            "timestamp": time.time(),
            "level": "error" if error else "debug",
            "category": f"node_{action}",
            "node": node_name,
            "message": message,
        }
        if duration_ms:
            entry["duration_ms"] = duration_ms
        if error:
            entry["error"] = error
        loop = asyncio.new_event_loop()
        loop.run_until_complete(db.debug_traces.insert_one(entry))
        loop.close()
        mc.close()
    except Exception:
        pass  # non-fatal
