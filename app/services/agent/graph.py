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

  - ANALYZE path: retrieve → synthesis → END
  - REASON path:  retrieve → graph_expansion → defined_terms → contradiction
                          → findings → escalation → result → END
  - ACT path:     detect → decision_brief   [HITL pause 1: lawyer confirms brief]
                        → draft             [HITL pause 2: lawyer approves draft]
                        → export → END
"""

import logging

from langgraph.graph import END, START, StateGraph

from app.models import WorkflowStatus
from app.services.agent.agent_state import PointerOnlyState
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


def create_action_agent_graph() -> StateGraph:
    """
    Returns the compiled graph (without checkpointer — injected at call site).

    ACT path interrupt_before points (passed at compile time by the router):
      - "decision_brief": graph pauses before running the node; lawyer reviews
        retrieved context and the brief; resumes when brief_confirmed=True is
        injected via aupdate_state + ainvoke(None, ...).
      - "draft": graph pauses before generating the draft; lawyer has confirmed
        the brief; resumes when the approve endpoint calls ainvoke(None, ...).
    """
    workflow = StateGraph(PointerOnlyState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    workflow.add_node("intent", intent_node)
    workflow.add_node("ambiguity_gate", ambiguity_gate_node)

    # ANALYZE / REASON shared retrieval
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

    # ACT path (new 3-phase pipeline)
    workflow.add_node("detect", detect_node)
    workflow.add_node("decision_brief", decision_brief_node)  # HITL pause 1
    workflow.add_node("draft", draft_node)                    # HITL pause 2
    workflow.add_node("export", export_node)

    # ── Edges ──────────────────────────────────────────────────────────────
    workflow.add_edge(START, "intent")

    # Intent routing
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

    # Post-ambiguity gate
    workflow.add_conditional_edges(
        "ambiguity_gate",
        _post_ambiguity_router,
        {
            "analyze_path": "retrieve",
            "reason_path": "retrieve",
            "act_path": "detect",
        },
    )

    # Shared retrieval: differentiates ANALYZE vs REASON
    def _post_retrieve_router(state: PointerOnlyState) -> str:
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

    # ACT edges (new pipeline — no saga, no execution loop)
    workflow.add_edge("detect", "decision_brief")   # graph pauses BEFORE decision_brief (HITL 1)
    workflow.add_edge("decision_brief", "draft")    # graph pauses BEFORE draft (HITL 2)
    workflow.add_edge("draft", "export")
    workflow.add_edge("export", END)

    return workflow
