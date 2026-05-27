"""
PointerOnlyState — The Index Card
===================================
The LangGraph state must be kept minimal. Heavy data (document text, drafts,
API payloads) is NEVER stored here. Only UUIDs and small metadata survive in
the checkpoint.

Schema versioning: include `graph_version` so old checkpoints can be adapted
when the graph evolves (via an adapter function before node entry).
"""

import uuid
from typing import Annotated, List, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

CURRENT_GRAPH_VERSION = "1.0"


class PointerOnlyState(TypedDict):
    # ── Schema versioning ─────────────────────────────────────────────────────
    graph_version: str  # e.g. "1.0" — used by adapter on resume

    # ── Core identity pointers ────────────────────────────────────────────────
    workflow_id: str  # UUID as str (serializable in checkpoint)
    workspace_id: str
    org_id: str
    document_id: str

    # --- Intent State (Master Orchestrator) ---
    primary_intent: Optional[str]  # "ANALYZE" | "REASON" | "ACT"
    intent_confidence: float  # 0.0 - 1.0
    intent_confirmed_by_human: bool  # True if ambiguity gate was triggered and resolved
    goal_text: str  # The original user goal

    # Context passing for paths
    context_text: Optional[str]

    # ── Execution pointers ────────────────────────────────────────────────────
    plan_id: Optional[str]  # UUID pointer to TaskPlan row in DB
    current_task_index: int
    total_tasks: int

    # ── Human-readable state for frontend polling ─────────────────────────────
    status: str  # mirrors WorkflowStatus enum value

    # ── Lightweight findings summary (never raw text) ─────────────────────────
    findings_summary: str  # one-line summary written by detect_node
    action_count: int

    # ── Message history — pruned aggressively after major checkpoints ─────────
    messages: Annotated[List[BaseMessage], add_messages]

    # ── Error tracking ────────────────────────────────────────────────────────
    error_context: Optional[str]
    retry_count: int

    # ── Document scoping ───────────────────────────────────────────────────────
    session_file_ids: list[int]  # Qdrant file_ids from the Redis session

    # ── ACT path Phase 1 & 2 gate flags ──────────────────────────────────────
    brief_confirmed: Optional[bool]   # True after lawyer clicks "proceed to draft"
    draft_r2_key: Optional[str]       # R2 key for the exported DOCX after approval
