"""
Action Agent — LangGraph Graph Definition (Master Orchestrator)
================================================================
Compiles the StateGraph with intent classification, ambiguity gating,
and dynamic routing to ANALYZE, REASON, or ACT capability paths.

Routing logic:
  - START → intent_node
  - intent_node → ambiguity_check
    - if confidence < 0.8: route to ambiguity_gate_node (HITL pause)
    - if confidence >= 0.8: route by primary_intent (ANALYZE / REASON / ACT)

  - ambiguity_gate_node → route by primary_intent (resumes here after user confirms)

  - ANALYZE path: retrieval_node → synthesis_node → END
  - REASON path: retrieval_node → graph_expansion_node → findings_node → END
  - ACT path: detect_node → draft_node → plan_node → human_approval_node (HITL pause) → execute_node (loop) → END
"""

import logging

from app.models import WorkflowStatus
from app.services.agent.agent_state import PointerOnlyState
from app.services.agent.nodes import (
    ambiguity_gate_node,
    compensate_node,
    contradiction_node,
    defined_terms_node,
    detect_node,
    draft_node,
    escalation_node,
    execute_node,
    findings_node,
    graph_expansion_node,
    human_approval_node,
    intent_node,
    plan_node,
    result_node,
    retrieval_node,
    synthesis_node,
)
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)


def _ambiguity_router(state: PointerOnlyState) -> str:
    """Routes after intent_node: Ambiguity gate or capability path."""
    confidence = state.get("intent_confidence", 0.0)
    confirmed = state.get("intent_confirmed_by_human", False)

    if confidence < 0.80 and not confirmed:
        return "ambiguity_gate"

    intent = state.get("primary_intent", "ANALYZE")
    if intent == "ANALYZE":
        return "analyze_path"
    elif intent == "REASON":
        return "reason_path"
    elif intent == "ACT":
        return "act_path"

    # Fallback
    return "analyze_path"


def _post_ambiguity_router(state: PointerOnlyState) -> str:
    """Routes after human confirms intent at the ambiguity gate."""
    intent = state.get("primary_intent", "ANALYZE")
    if intent == "ANALYZE":
        return "analyze_path"
    elif intent == "REASON":
        return "reason_path"
    elif intent == "ACT":
        return "act_path"
    return "analyze_path"


def _execution_router(state: PointerOnlyState) -> str:
    """
    Routes after execute_node (ACT path).
    - Loop back to execute if tasks remain.
    - End if status is COMPLETED or FAILED.
    """
    status = state.get("status", "")
    current = state.get("current_task_index", 0)
    total = state.get("total_tasks", 0)

    if status == WorkflowStatus.RECOVERING.value:
        return "compensate"
    if status in (WorkflowStatus.COMPLETED.value, WorkflowStatus.FAILED.value):
        return END
    if current >= total:
        return END
    return "execute"


def create_action_agent_graph() -> StateGraph:
    """
    Returns the compiled graph (without checkpointer — injected at call time).
    """
    workflow = StateGraph(PointerOnlyState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    workflow.add_node("intent", intent_node)
    workflow.add_node("ambiguity_gate", ambiguity_gate_node)

    # ANALYZE / REASON shared nodes
    workflow.add_node("retrieve", retrieval_node)

    # ANALYZE path
    workflow.add_node("synthesis", synthesis_node)

    # REASON path
    workflow.add_node("graph_expansion", graph_expansion_node)
    workflow.add_node("defined_terms", defined_terms_node)
    workflow.add_node("contradiction", contradiction_node)
    workflow.add_node("findings", findings_node)
    workflow.add_node("escalation", escalation_node)
    workflow.add_node("result", result_node)

    # ACT path
    workflow.add_node("detect", detect_node)
    workflow.add_node("draft", draft_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("execute", execute_node)
    workflow.add_node("compensate", compensate_node)

    # ── Edges ──────────────────────────────────────────────────────────────
    workflow.add_edge(START, "intent")

    # Ambiguity Check
    workflow.add_conditional_edges(
        "intent",
        _ambiguity_router,
        {
            "ambiguity_gate": "ambiguity_gate",
            "analyze_path": "retrieve",
            "reason_path": "retrieve",
            "act_path": "detect",
        },
    )

    # Post-Ambiguity Gate
    workflow.add_conditional_edges(
        "ambiguity_gate",
        _post_ambiguity_router,
        {
            "analyze_path": "retrieve",
            "reason_path": "retrieve",
            "act_path": "detect",
        },
    )

    # Shared Retrieval router (differentiates ANALYZE vs REASON)
    def _post_retrieve_router(state: PointerOnlyState) -> str:
        # UNDETERMINED: skip synthesis if retrieval found < 3 relevant chunks
        if state.get("retrieval_aborted", False):
            return "undetermined"
        if state.get("primary_intent") == "REASON":
            return "reason_expansion"
        return "synthesis"

    workflow.add_conditional_edges(
        "retrieve",
        _post_retrieve_router,
        {
            "synthesis": "synthesis",
            "reason_expansion": "graph_expansion",
            "undetermined": END,
        },
    )

    # ANALYZE edges
    workflow.add_edge("synthesis", END)

    # REASON edges
    workflow.add_edge("graph_expansion", "defined_terms")
    workflow.add_edge("defined_terms", "contradiction")
    workflow.add_edge("contradiction", "findings")
    workflow.add_edge("findings", "escalation")
    workflow.add_edge("escalation", "result")
    workflow.add_edge("result", END)

    # ACT edges
    workflow.add_edge("detect", "draft")
    workflow.add_edge("draft", "plan")
    workflow.add_edge("plan", "human_approval")  # graph pauses BEFORE this node
    workflow.add_edge("human_approval", "execute")

    # Execution loop: keep running tasks until complete
    workflow.add_conditional_edges(
        "execute",
        _execution_router,
        {
            "execute": "execute",
            "compensate": "compensate",
            END: END,
        },
    )

    # Saga failure end
    workflow.add_edge("compensate", END)

    return workflow
