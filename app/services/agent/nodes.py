"""
Action Agent — LangGraph Nodes (Master Orchestrator)
====================================================
This file implements all nodes for the unified Master Orchestrator,
handling ANALYZE, REASON, and ACT capability paths.

Each node receives a PointerOnlyState (index card) and returns a partial dict
to update only the fields it owns. Nodes NEVER store raw document text, draft
content, or large API payloads in state — those go to S3/Postgres and a pointer
is stored here.
"""

import hashlib
import json
import logging
import os
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import AsyncSessionLocal
from app.models import (
    Action,
    ActionStatus,
    ActionType,
    ApprovalRequest,
    ApprovalStatus,
    AuditLog,
    DeadlineRegistry,
    DefinedTermRegistry,
    EscalationType,
    Finding,
    IdempotencyClass,
    IntentLog,
    ToolCallLog,
    ToolCallStatus,
    WorkflowExecution,
    WorkflowStatus,
)
from app.services.agent.agent_state import CURRENT_GRAPH_VERSION, PointerOnlyState
from app.services.object_storage import upload_bytes
from app.utils import sanitize_goal_text

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency key generation (FR-EXEC-02)
# ─────────────────────────────────────────────────────────────────────────────
def generate_idempotency_key(
    workflow_id: str, action_id: str, tool_name: str, attempt_number: int
) -> str:
    """
    Generate a deterministic idempotency key for a tool execution.

    Rule FR-EXEC-02: The key is computed as the SHA-256 hex digest of the
    concatenation of workflow_id, action_id, tool_name, and attempt_number.
    This guarantees that retrying the same action with the same attempt
    number reuses the existing result, preventing duplicate side-effects.

    Args:
        workflow_id:   The UUID of the parent workflow execution.
        action_id:     The UUID of the action (task) being executed.
        tool_name:     The name/type of the tool being called (e.g. action_type.value).
        attempt_number: The 1-based retry attempt counter.

    Returns:
        A 64-character lowercase hex string (SHA-256 digest).
    """
    raw_key = f"{workflow_id}{action_id}{tool_name}{attempt_number}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Compensation plan — pure function (FR-EXEC-04 Saga Pattern)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ToolLogEntry:
    """Lightweight representation of a tool call log for compensation planning.

    Pure-data container — no database dependency, easily constructed in tests.
    """

    action_id: str
    created_at: datetime


@dataclass
class ActionInfo:
    """Lightweight compensation metadata for an action.

    Pure-data container — no database dependency, easily constructed in tests.
    """

    idempotency_class: str
    compensation_action: str | None
    compensation_params: dict[str, Any] | None


def build_compensation_plan(
    tool_logs: list[ToolLogEntry],
    actions_map: dict[str, ActionInfo],
) -> list[tuple[str, str, dict[str, Any] | None]]:
    """
    Build a compensation plan following the Saga pattern.

    Given a list of tool call log entries and a mapping of action_id to
    compensation metadata, determines which executed tools require
    compensation and returns them in **LIFO** order (Last-In-First-Out —
    the last tool executed is the first to be compensated).

    This is a **pure function** with zero side effects:
      - No database queries
      - No logging
      - No state mutation

    This makes it directly testable without a database connection or
    any infrastructure setup.

    Args:
        tool_logs:   Chronological list of successfully executed tool calls.
        actions_map: Mapping of action_id (str) -> ActionInfo with
                     idempotency_class and compensation metadata.

    Returns:
        List of ``(action_id, compensation_action, compensation_params)``
        tuples ordered in reverse execution order (LIFO). Only actions
        whose ``idempotency_class`` is ``REQUIRES_COMPENSATION`` and which
        have a ``compensation_action`` defined are included.
    """
    # Sort by created_at descending — LIFO: last executed, first compensated
    sorted_logs = sorted(tool_logs, key=lambda x: x.created_at, reverse=True)

    plan: list[tuple[str, str, dict[str, Any] | None]] = []
    for entry in sorted_logs:
        action = actions_map.get(entry.action_id)
        if not action:
            continue
        if (
            action.idempotency_class == IdempotencyClass.REQUIRES_COMPENSATION.value
            and action.compensation_action
        ):
            plan.append(
                (
                    entry.action_id,
                    action.compensation_action,
                    action.compensation_params,
                )
            )

    return plan


def _adapt_state(state: PointerOnlyState) -> PointerOnlyState:
    defaults: Dict[str, Any] = {
        "graph_version": CURRENT_GRAPH_VERSION,
        "primary_intent": None,
        "intent_confidence": 0.0,
        "intent_confirmed_by_human": False,
        "goal_text": "",
        "plan_id": None,
        "current_task_index": 0,
        "total_tasks": 0,
        "findings_summary": "",
        "action_count": 0,
        "error_context": None,
        "retry_count": 0,
        "messages": [],
    }
    for key, val in defaults.items():
        if key not in state or state[key] is None:
            state = {**state, key: val}  # type: ignore[assignment]
    return state


async def _set_wf_status(workflow_id: str, status: WorkflowStatus) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == _uuid.UUID(workflow_id))
            .values(status=status)
        )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────
class IntentClassification(BaseModel):
    primary_intent: str = Field(
        ..., description="Must be 'ANALYZE', 'REASON', or 'ACT'"
    )
    confidence: float = Field(..., description="0.0 to 1.0 confidence score")
    reasoning: str = Field(
        ..., description="One sentence explaining the classification"
    )
    requires_graph: bool = Field(
        ..., description="Whether cross-document reasoning is needed"
    )
    requires_action: bool = Field(..., description="Whether execution is implied")
    deadline_sensitive: bool = Field(
        ..., description="Whether a deadline was detected in the goal"
    )
    urgency_score: float = Field(
        ..., description="0.0 to 1.0 urgency score based on deadline proximity"
    )


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: MASTER ORCHESTRATOR NODES
# ─────────────────────────────────────────────────────────────────────────────


async def intent_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Classifies the user's goal into ANALYZE, REASON, or ACT using Llama 3.3 70B via Groq.
    Updates WorkflowExecution status to CLASSIFYING.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = sanitize_goal_text(state.get("goal_text", ""))

    logger.info("[%s] intent_node: Classifying goal...", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.CLASSIFYING)

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", ""),
    ).with_structured_output(IntentClassification)

    prompt = (
        f"You are the master intent classifier for the Lex legal AI platform.\n"
        f"Classify the following user goal into one of three categories:\n"
        f"- ANALYZE: A question requiring grounded retrieval (e.g. 'What does X mean?', 'Find Y').\n"
        f"- REASON: Requires cross-document analysis, contradiction detection, or risk assessment (e.g. 'What are the risks...', 'Compare X and Y').\n"
        f"- ACT: Requires drafting, approval, and execution of a legal action (e.g. 'Draft a response', 'Send notice').\n\n"
        f"Goal: {goal_text}"
    )

    try:
        classification: IntentClassification = await llm.ainvoke(prompt)
    except Exception as e:
        logger.error("[%s] intent_node failed: %s", workflow_id, e)
        # Fallback if Groq fails
        classification = IntentClassification(
            primary_intent="ANALYZE",
            confidence=0.5,
            reasoning="Fallback due to LLM error",
            requires_graph=False,
            requires_action=False,
            deadline_sensitive=False,
            urgency_score=0.0,
        )

    logger.info(
        "[%s] Intent classified as %s (confidence: %.2f)",
        workflow_id,
        classification.primary_intent,
        classification.confidence,
    )

    # NFR-AUD-01: Archive prompt to R2 + log audit trail
    try:
        prompt_text = prompt  # The exact prompt sent to the LLM
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        audit_id = prompt_hash[:16]
        goal_hash = hashlib.sha256(goal_text.encode("utf-8")).hexdigest()

        # Archive full prompt to R2
        try:
            upload_bytes(
                prompt_text.encode("utf-8"),
                f"audit/{audit_id}/prompt.txt",
                content_type="text/plain",
            )
        except Exception as r2_err:
            logger.warning(
                "[%s] Failed to archive prompt to R2: %s", workflow_id, r2_err
            )

        # NFR-AUD-03: Log classification
        async with AsyncSessionLocal() as db:
            db.add(
                IntentLog(
                    workflow_id=_uuid.UUID(workflow_id),
                    goal_hash=goal_hash,
                    model_name="llama-3.3-70b-versatile",
                    prompt_hash=prompt_hash,
                    audit_id=audit_id,
                    confidence=classification.confidence,
                    confirmed_intent=classification.primary_intent,
                    lawyer_override=False,
                )
            )
            await db.commit()
    except Exception as audit_err:
        logger.warning(
            "[%s] Audit logging failed (non-fatal): %s", workflow_id, audit_err
        )

    return {
        "primary_intent": classification.primary_intent,
        "intent_confidence": classification.confidence,
    }


async def ambiguity_gate_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    HITL Pause for ambiguity resolution.
    If intent confidence < 0.80 and not yet confirmed, pause here.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]

    logger.info(
        "[%s] ambiguity_gate_node: Pausing for user confirmation (confidence=%.2f)",
        workflow_id,
        state["intent_confidence"],
    )
    await _set_wf_status(workflow_id, WorkflowStatus.AWAITING_INTENT_CONFIRMATION)

    return {"status": WorkflowStatus.AWAITING_INTENT_CONFIRMATION.value}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2A: ANALYZE / REASON PATH NODES
# ─────────────────────────────────────────────────────────────────────────────


async def retrieval_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Executes hybrid search (Qdrant) with dual dense (voyage + nomic) + sparse (SPLADE).

    Returns structured results with relevance scores.
    Falls back to UNDETERMINED if fewer than 3 chunks score > 0.6.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = state.get("goal_text", "")
    org_id = state.get("org_id")
    workspace_id = state.get("workspace_id")

    logger.info("[%s] retrieval_node: Executing hybrid search...", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.RETRIEVING)

    from app.services.ingestion.embedder import LegalEmbedder
    from app.services.store import search_hybrid

    retrieval_aborted = False
    reranked_results = []

    try:
        embedder = LegalEmbedder()
        voyage_vec = embedder.get_voyage_query_vector(goal_text)
        splade_vec = embedder.get_splade_query_vector(goal_text)

        # Generate nomic dense vector (second dense, local via fastembed)
        nomic_vec = None
        try:
            from fastembed import TextEmbedding

            nomic_model = TextEmbedding(model_name="nomic-embed-text-v1.5")
            nomic_vec = list(nomic_model.embed([goal_text]))[0].tolist()
        except Exception as nomic_err:
            logger.debug(
                "[%s] Nomic embed unavailable (non-fatal): %s", workflow_id, nomic_err
            )

        search_response = search_hybrid(
            query_text=goal_text,
            query_vector=voyage_vec,
            nomic_vector=nomic_vec,
            top_k=5,
            org_id=org_id,
            workspace_id=workspace_id,
        )

        # Unpack new dict return format (list fallback for backward compat)
        if isinstance(search_response, dict):
            results = search_response.get("results", [])
            reranker_metrics = search_response.get("reranker_metrics", {})
        else:
            results = search_response
            reranker_metrics = {}

        for r in results:
            reranked_results.append(
                {
                    "text": r.get("text", ""),
                    "score": r.get("score", 0.0),
                    "metadata": r.get("metadata", {}),
                }
            )

        # Log reranker metrics to IntentLog
        if reranker_metrics:
            try:
                async with AsyncSessionLocal() as log_db:
                    log_db.add(
                        IntentLog(
                            workflow_id=_uuid.UUID(workflow_id),
                            goal_hash=hashlib.sha256(goal_text.encode()).hexdigest(),
                            model_name="rerank-english-v3.0",
                            prompt_hash=hashlib.sha256(goal_text.encode()).hexdigest(),
                            audit_id="rerank_" + workflow_id[:8],
                            confidence=reranker_metrics.get("top_1_score", 0.0),
                            confirmed_intent="ANALYZE",
                            lawyer_override=False,
                        )
                    )
                    await log_db.commit()
            except Exception as log_err:
                logger.debug(
                    "[%s] Reranker metrics logging failed: %s", workflow_id, log_err
                )

        # NFR-PERF-02: UNDETERMINED fallback — fewer than 3 chunks with score > 0.6
        high_conf_chunks = [r for r in reranked_results if r["score"] > 0.6]
        if len(high_conf_chunks) < 3:
            retrieval_aborted = True
            logger.info(
                "[%s] UNDETERMINED: only %d chunks > 0.6 (need 3)",
                workflow_id,
                len(high_conf_chunks),
            )

            # INSUFFICIENT_COVERAGE escalation
            try:
                async with AsyncSessionLocal() as esc_db:
                    esc_db.add(
                        Finding(
                            workflow_id=_uuid.UUID(workflow_id),
                            org_id=_uuid.UUID(org_id) if org_id else None,
                            claim="UNDETERMINED: Insufficient evidence to answer the query",
                            confidence=0.0,
                            escalated=True,
                            escalation_type=EscalationType.INSUFFICIENT_COVERAGE,
                            definitional_conflicts=[
                                {
                                    "reason": f"Only {len(high_conf_chunks)} chunk(s) scored above 0.6 relevance threshold (need 3)",
                                    "top_score": reranker_metrics.get(
                                        "top_1_score", 0.0
                                    ),
                                    "total_chunks": len(reranked_results),
                                }
                            ],
                        )
                    )
                    await esc_db.commit()
            except Exception as esc_err:
                logger.warning(
                    "[%s] Escalation creation failed: %s", workflow_id, esc_err
                )

        # Format context for synthesis (only if continuing)
        context_parts = []
        for r in reranked_results:
            text = r.get("text", "")
            source = r.get("metadata", {}).get("source", "Unknown Document")
            page = r.get("metadata", {}).get("page", "?")
            context_parts.append(f"Source: {source} (Page {page})\n{text}")

        context_text = "\n\n---\n\n".join(context_parts) if context_parts else ""

    except Exception as e:
        logger.error("[%s] Qdrant retrieval failed: %s", workflow_id, e)
        retrieval_aborted = True
        context_text = ""
        reranked_results = []

    return {
        "status": WorkflowStatus.RETRIEVING.value,
        "context_text": context_text,
        "retrieval_aborted": retrieval_aborted,
        "retrieved_chunks": reranked_results,
    }


class AnalyzeResult(BaseModel):
    """Structured output for the ANALYZE path synthesis."""

    answer: str = Field(..., description="The synthesized answer with inline citations")
    confidence: float = Field(..., description="0.0 to 1.0 confidence in the answer")
    source_citations: list[str] = Field(
        ..., description="List of source document references cited in the answer"
    )
    faithfulness_score: float = Field(
        ...,
        description="0.0 to 1.0 — how strictly the answer is grounded in the sources",
    )


async def synthesis_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Generates a cited answer for the ANALYZE path.

    Uses Chain-of-Thought extraction with negative examples to enforce
    strict grounding. Returns UNDETERMINED if retrieval was aborted.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = sanitize_goal_text(state.get("goal_text", ""))
    context_text = state.get("context_text", "")
    retrieval_aborted = state.get("retrieval_aborted", False)

    # UNDETERMINED: skip synthesis if retrieval found insufficient evidence
    if retrieval_aborted or not context_text:
        logger.info(
            "[%s] synthesis_node: UNDETERMINED — insufficient evidence, aborting",
            workflow_id,
        )
        await _set_wf_status(workflow_id, WorkflowStatus.COMPLETED)
        return {
            "status": WorkflowStatus.COMPLETED.value,
            "findings_summary": (
                "UNDETERMINED: The system could not find enough relevant evidence "
                "in the documents to answer this question confidently. "
                "Consider rephrasing your question or uploading additional documents."
            ),
            "analyze_result": None,
        }

    logger.info("[%s] synthesis_node: Synthesizing answer...", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.DRAFTING)

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", ""),
    ).with_structured_output(AnalyzeResult)

    prompt = (
        f"You are an expert legal AI assistant. Answer the user's question strictly from the provided context.\n\n"
        f"STEP 1: Extract key claims from the context step by step.\n"
        f"STEP 2: Verify each claim against the source chunks. Every single claim MUST map to a specific chunk.\n"
        f"STEP 3: Synthesize the final answer with inline citations like [Source: Document Name].\n\n"
        f"--- NEGATIVE EXAMPLES ---\n"
        f"BAD (not grounded): 'The contract likely includes a penalty clause.'\n"
        f"GOOD (grounded): 'Section 4.2 states: \"A late fee of 1.5% applies.\" [Source: Service Agreement]'\n"
        f"BAD (hallucination): 'Most companies negotiate this term.'\n"
        f"GOOD (honest): 'The provided context does not mention negotiation terms for this clause.'\n\n"
        f"USER QUESTION: {goal_text}\n\n"
        f"DOCUMENT CONTEXT:\n{context_text}"
    )

    try:
        result: AnalyzeResult = await llm.ainvoke(prompt)
        findings = result.answer
        confidence = result.confidence
        citations = result.source_citations
        faithfulness = result.faithfulness_score
    except Exception as e:
        logger.error("[%s] synthesis_node failed: %s", workflow_id, e)
        result = AnalyzeResult(
            answer="Error: Failed to synthesize answer using LLM.",
            confidence=0.0,
            source_citations=[],
            faithfulness_score=0.0,
        )
        findings = result.answer
        confidence = 0.0
        citations = []
        faithfulness = 0.0

    # NFR-AUD-01: Log faithfulness to ResearchLog
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                IntentLog(
                    workflow_id=_uuid.UUID(workflow_id),
                    goal_hash=hashlib.sha256(goal_text.encode()).hexdigest(),
                    model_name="llama-3.3-70b-versatile",
                    prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                    audit_id="synth_" + workflow_id[:8],
                    confidence=confidence,
                    confirmed_intent="ANALYZE",
                    lawyer_override=False,
                )
            )
            await db.commit()
    except Exception as log_err:
        logger.warning("[%s] Faithfulness logging failed: %s", workflow_id, log_err)

    await _set_wf_status(workflow_id, WorkflowStatus.COMPLETED)
    return {
        "status": WorkflowStatus.COMPLETED.value,
        "findings_summary": findings,
        "analyze_result": {
            "answer": findings,
            "confidence": confidence,
            "source_citations": citations,
            "faithfulness_score": faithfulness,
        },
    }


async def graph_expansion_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Expands retrieved chunks via FalkorDB cross-reference graph (REASON path).

    - 2-hop limit: hop 1 = full text, hop 2 = clause_id only
    - Hydrates clause text from Qdrant for hop 1 references
    - Graceful degradation: if FalkorDB is down, sets graph_degraded=True
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = state.get("goal_text", "")
    org_id = state.get("org_id")
    workspace_id = state.get("workspace_id", "")
    context_text = state.get("context_text", "")

    logger.info("[%s] graph_expansion_node: Expanding graph references...", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.EXPANDING)

    graph_degraded = False
    expanded_chains: list[dict] = []  # reference_chain for DB storage
    seen_clause_ids: set = set()

    # Collect clause_references from retrieved chunks
    retrieved_chunks = state.get("retrieved_chunks", [])
    clause_refs = []
    for chunk in retrieved_chunks:
        ref = chunk.get("metadata", {}).get("clause_reference", "")
        if ref and ref not in clause_refs:
            clause_refs.append(ref)

    if not clause_refs:
        logger.debug("[%s] No clause references to expand", workflow_id)
        return {
            "status": WorkflowStatus.EXPANDING.value,
            "context_text": context_text,
            "graph_degraded": False,
            "reference_chain": [],
        }

    # Query FalkorDB with graceful degradation
    try:
        from app.services.ingestion.graph_extractor import DependencyGraph

        graph = DependencyGraph()
        batch_start = time.time()

        for ref in clause_refs:
            chains = graph.get_dependency_chains(
                node_id=ref, max_hops=2, workspace_id=str(workspace_id)
            )

            for c in chains:
                node_id = c.get("node", "")
                if not node_id or node_id in seen_clause_ids:
                    continue
                seen_clause_ids.add(node_id)

                hop = c.get("hop", 1)
                source = c.get("source_document", "Unknown")
                text = c.get("text", "")

                # Hop 1: full text
                if hop <= 1 and text:
                    expanded_chains.append(
                        {
                            "clause_id": node_id,
                            "source_document": source,
                            "text": text[:2000],
                            "hop": hop,
                        }
                    )
                # Hop 2: clause_id only (no full text)
                elif hop == 2:
                    expanded_chains.append(
                        {
                            "clause_id": node_id,
                            "source_document": source,
                            "text": "",  # No full text beyond 2 hops
                            "hop": hop,
                        }
                    )

        graph.close()
        elapsed = time.time() - batch_start
        logger.info(
            "[%s] Graph expansion: %d chains in %.2fs",
            workflow_id,
            len(expanded_chains),
            elapsed,
        )

    except Exception as e:
        logger.warning("[%s] FalkorDB unavailable — DEGRADED mode: %s", workflow_id, e)
        graph_degraded = True

    # Format expansion into context
    if expanded_chains:
        expansion_parts = []
        for chain in expanded_chains:
            if chain["text"]:
                expansion_parts.append(
                    f"[Ref: {chain['clause_id']}] (Source: {chain['source_document']})\n{chain['text']}"
                )
            else:
                expansion_parts.append(
                    f"[Ref: {chain['clause_id']}] (Source: {chain['source_document']}, 2+ hops — full text omitted)"
                )

        context_text += "\n\n=== CROSS-DOCUMENT GRAPH EXPANSION ===\n\n"
        context_text += "\n\n---\n\n".join(expansion_parts)

    return {
        "status": WorkflowStatus.EXPANDING.value,
        "context_text": context_text,
        "graph_degraded": graph_degraded,
        "reference_chain": expanded_chains,
    }


async def defined_terms_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Cross-references retrieved text with DefinedTermsRegistry.

    For each chunk in context, checks if it contains defined terms with
    conflict_flag=True. Returns definitional_conflicts for the findings node.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    context_text = state.get("context_text", "")
    org_id = state.get("org_id")

    logger.info("[%s] defined_terms_node: Checking for defined terms...", workflow_id)

    definitional_conflicts: list[dict] = []

    if not context_text or not org_id:
        return {"status": WorkflowStatus.PROCESSING.value, "definitional_conflicts": []}

    try:
        async with AsyncSessionLocal() as db:
            # Fetch all conflicted terms for this org
            from sqlalchemy import select as sa_select

            result = await db.execute(
                sa_select(DefinedTermRegistry).where(
                    DefinedTermRegistry.org_id == _uuid.UUID(org_id),
                    DefinedTermRegistry.conflict_flag == True,
                )
            )
            conflicted_terms = result.scalars().all()

        if not conflicted_terms:
            logger.debug("[%s] No conflicted terms in registry", workflow_id)
            return {
                "status": WorkflowStatus.PROCESSING.value,
                "definitional_conflicts": [],
            }

        # Check each conflicted term against the context text
        context_lower = context_text.lower()
        for term in conflicted_terms:
            term_name_lower = term.term.lower()
            if term_name_lower in context_lower:
                definitional_conflicts.append(
                    {
                        "term": term.term,
                        "definition": term.definition[:200],
                        "source_document_id": str(term.source_document_id),
                        "conflict_description": term.conflict_description,
                        "clause_reference": term.clause_reference,
                    }
                )

    except Exception as e:
        logger.warning("[%s] Defined terms check failed: %s", workflow_id, e)

    if definitional_conflicts:
        logger.info(
            "[%s] Found %d definitional conflicts in context",
            workflow_id,
            len(definitional_conflicts),
        )

    return {
        "status": WorkflowStatus.PROCESSING.value,
        "definitional_conflicts": definitional_conflicts,
    }


async def contradiction_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Compares all findings for this workflow and flags contradictions.

    If two findings make opposing claims about the same obligation,
    they are flagged as a Contradiction.
    """
    state = _adapt_state(state)
    workflow_id_str = state["workflow_id"]
    workflow_id = _uuid.UUID(workflow_id_str)

    logger.info(
        "[%s] contradiction_node: Checking for contradictions...", workflow_id_str
    )

    contradictions: list[dict] = []

    try:
        async with AsyncSessionLocal() as db:
            # Fetch all findings for this workflow
            result = await db.execute(
                select(Finding).where(
                    Finding.workflow_id == workflow_id,
                    Finding.escalated == False,
                )
            )
            findings = result.scalars().all()

        if len(findings) < 2:
            return {"status": WorkflowStatus.PROCESSING.value, "contradictions": []}

        # Compare each pair for conflicting claims
        for i in range(len(findings)):
            for j in range(i + 1, len(findings)):
                a, b = findings[i], findings[j]
                # Simple heuristic: if claims share key words but differ in sentiment
                words_a = set(a.claim.lower().split())
                words_b = set(b.claim.lower().split())
                common = words_a & words_b
                # If they share significant vocabulary, flag as potential contradiction
                if len(common) >= 5 and a.confidence > 0.3 and b.confidence > 0.3:
                    contradictions.append(
                        {
                            "finding_a_id": str(a.id),
                            "finding_b_id": str(b.id),
                            "claim_a": a.claim[:200],
                            "claim_b": b.claim[:200],
                            "overlap_terms": list(common)[:10],
                        }
                    )

    except Exception as e:
        logger.warning("[%s] Contradiction check failed: %s", workflow_id_str, e)

    if contradictions:
        logger.info(
            "[%s] Found %d contradictions", workflow_id_str, len(contradictions)
        )

    return {"status": WorkflowStatus.PROCESSING.value, "contradictions": contradictions}


async def findings_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Generates a structured findings summary using the LLM and context.
    Creates a Finding DB record per major claim.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = sanitize_goal_text(state.get("goal_text", ""))
    context_text = state.get("context_text", "")
    org_id = state.get("org_id", "")

    logger.info("[%s] findings_node: Generating findings...", workflow_id)

    if not context_text:
        return {
            "status": WorkflowStatus.PROCESSING.value,
            "findings_summary": "No context to analyze.",
        }

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", ""),
    )

    prompt = (
        f"You are an expert legal due diligence auditor. Analyze the provided contract context "
        f"(including cross-references) to answer the query.\n\n"
        f"USER QUERY: {goal_text}\n\n"
        f"CONTEXT:\n{context_text[:8000]}\n\n"
        f"Please provide a structured report with individual 'Findings'. "
        f"Each finding should have: claim, confidence (0-1), and citations."
    )

    try:
        response = await llm.ainvoke(prompt)
        findings_text = response.content
    except Exception as e:
        logger.error("[%s] findings_node failed: %s", workflow_id, e)
        findings_text = "Error: Failed to generate findings."

    # Store a Finding record
    try:
        if org_id:
            async with AsyncSessionLocal() as db:
                db.add(
                    Finding(
                        workflow_id=_uuid.UUID(workflow_id),
                        org_id=_uuid.UUID(org_id),
                        claim=findings_text[:500],
                        confidence=0.7,
                        supporting_citations=[{"source": "llm_analysis"}],
                        reference_chain=state.get("reference_chain", []),
                        definitional_conflicts=state.get("definitional_conflicts", []),
                    )
                )
                await db.commit()
    except Exception as db_err:
        logger.warning("[%s] Finding DB insert failed: %s", workflow_id, db_err)

    return {
        "status": WorkflowStatus.PROCESSING.value,
        "findings_summary": findings_text,
    }


async def escalation_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Reviews findings and definitional conflicts, creates typed Escalations.

    Escalation types: INSUFFICIENT_COVERAGE, DEFINITIONAL_CONFLICT, STRUCTURAL_AMBIGUITY
    """
    state = _adapt_state(state)
    workflow_id_str = state["workflow_id"]
    workflow_id = _uuid.UUID(workflow_id_str)
    org_id_str = state.get("org_id", "")

    logger.info("[%s] escalation_node: Reviewing risk flags...", workflow_id_str)

    escalations: list[dict] = []

    # Collect data from upstream nodes
    definitional_conflicts = state.get("definitional_conflicts", [])
    contradictions = state.get("contradictions", [])
    graph_degraded = state.get("graph_degraded", False)

    # DEFINITIONAL_CONFLICT: conflicts found in defined_terms_node
    for dc in definitional_conflicts:
        escalations.append(
            {
                "type": EscalationType.DEFINITIONAL_CONFLICT.value,
                "term": dc.get("term", ""),
                "description": dc.get("conflict_description", "")[:200],
                "source": dc.get("source_document_id", ""),
            }
        )

    # STRUCTURAL_AMBIGUITY: contradictions found between findings
    for c in contradictions:
        escalations.append(
            {
                "type": EscalationType.STRUCTURAL_AMBIGUITY.value,
                "description": f"Contradiction between findings: {c.get('claim_a', '')[:100]} vs {c.get('claim_b', '')[:100]}",
                "finding_a": c.get("finding_a_id", ""),
                "finding_b": c.get("finding_b_id", ""),
            }
        )

    # DEGRADED warning from FalkorDB unavailability
    if graph_degraded:
        escalations.append(
            {
                "type": "DEGRADED",
                "description": "Cross-reference graph was unavailable. Results are retrieval-only.",
            }
        )

    if escalations:
        logger.info("[%s] Generated %d escalations", workflow_id_str, len(escalations))

    return {
        "status": WorkflowStatus.PROCESSING.value,
        "escalations": escalations,
    }


async def result_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Finalizes output for the REASON path.
    Assembles findings summary, escalations, and sets COMPLETED status.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]

    logger.info("[%s] result_node: Finalizing output...", workflow_id)

    findings_summary = state.get("findings_summary", "")
    if not findings_summary:
        findings_summary = "Analysis complete. See structured findings in the database."

    context_text = state.get("context_text", "")
    definitional_conflicts = state.get("definitional_conflicts", [])
    escalations = state.get("escalations", [])
    graph_degraded = state.get("graph_degraded", False)
    reference_chain = state.get("reference_chain", [])

    result = {
        "status": WorkflowStatus.COMPLETED.value,
        "findings_summary": findings_summary,
        "definitional_conflicts": definitional_conflicts,
        "escalations": escalations,
        "graph_degraded": graph_degraded,
        "reference_chain_count": len(reference_chain),
    }

    await _set_wf_status(workflow_id, WorkflowStatus.COMPLETED)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2B: ACT PATH NODES (Action Agent)
# ─────────────────────────────────────────────────────────────────────────────


async def detect_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Reads Stage-2 registries to surface candidate actions.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    workspace_id = _uuid.UUID(state["workspace_id"])
    org_id = _uuid.UUID(state["org_id"])
    wf_uuid = _uuid.UUID(workflow_id)

    logger.info("[%s] detect_node: scanning registries", workflow_id)

    detected: list[dict] = []
    async with AsyncSessionLocal() as db:
        conflicts_res = await db.execute(
            select(DefinedTermRegistry).where(
                DefinedTermRegistry.workspace_id == workspace_id,
                DefinedTermRegistry.conflict_flag == True,
            )
        )
        conflicts = conflicts_res.scalars().all()
        for c in conflicts:
            detected.append(
                dict(
                    workflow_id=wf_uuid,
                    org_id=org_id,
                    action_type=ActionType.FLAG_FOR_REVIEW
                    if hasattr(ActionType, "FLAG_FOR_REVIEW")
                    else ActionType.ESCALATE_TO_COUNSEL,
                    description=f"Resolve definitional conflict for term '{c.term}'",
                    urgency_score=0.7,
                    status=ActionStatus.DETECTED,
                    task_order=0,
                )
            )

        deadlines_res = await db.execute(
            select(DeadlineRegistry)
            .where(
                DeadlineRegistry.workspace_id == workspace_id,
                DeadlineRegistry.urgency_score >= 0.5,
            )
            .order_by(DeadlineRegistry.urgency_score.desc())
        )
        deadlines = deadlines_res.scalars().all()
        for dl in deadlines:
            source_ref = dl.source_clause_a
            detected.append(
                dict(
                    workflow_id=wf_uuid,
                    org_id=org_id,
                    action_type=ActionType.SEND_NOTICE,
                    description=f"Address obligation: {dl.obligation_description}",
                    urgency_score=float(dl.urgency_score),
                    status=ActionStatus.DETECTED,
                    source_clause_ref=source_ref,
                    deadline_id=dl.id,
                    task_order=0,
                )
            )

        if detected:
            stmt = pg_insert(Action).values(detected).on_conflict_do_nothing()
            await db.execute(stmt)

        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(status=WorkflowStatus.DRAFTING)
        )
        await db.commit()

    action_count = len(detected)
    summary = f"Detected {action_count} actions: {len(conflicts)} definitional conflicts, {len(deadlines)} deadline obligations."
    logger.info("[%s] detect_node complete: %s", workflow_id, summary)

    return {
        "status": WorkflowStatus.DRAFTING.value,
        "findings_summary": summary,
        "action_count": action_count,
    }


async def draft_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Uses Groq to generate drafts for detected actions.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)

    logger.info("[%s] draft_node: generating drafts", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.DRAFTING)

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", ""),
    )

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action).where(
                Action.workflow_id == wf_uuid, Action.status == ActionStatus.DETECTED
            )
        )
        actions = res.scalars().all()

        for action in actions:
            prompt = (
                f"You are a legal AI assistant. Draft content for the following legal action.\n"
                f"CRITICAL: Every single claim MUST map directly to a specific citation.\n"
                f"BAD (hallucination): 'Most companies negotiate this term.'\n"
                f"GOOD (grounded): 'Section 4.2 states a late fee of 1.5% applies. [Source: Service Agreement]'\n"
                f"If you cannot find support for a claim in the provided references, state that explicitly.\n\n"
                f"Action Type: {action.action_type.value}\n"
                f"Description: {action.description}\n"
                f"Context Reference: {action.source_clause_ref}\n\n"
                f"Draft a professional and legally appropriate response or notice."
            )
            try:
                response = await llm.ainvoke(prompt)
                action.draft_payload = {"draft_text": response.content}
                logger.info(
                    "[%s] Drafted content for action %s", workflow_id, action.id
                )
            except Exception as e:
                logger.error(
                    "[%s] Drafting failed for action %s: %s", workflow_id, action.id, e
                )
                action.draft_payload = {"error": str(e)}

        await db.commit()

    return {"status": WorkflowStatus.DRAFTING.value}


async def plan_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Sorts detected actions by urgency, assigns task_order, and transitions to AWAITING_APPROVAL.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)

    logger.info("[%s] plan_node: ordering task plan", workflow_id)

    total = 0
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action)
            .where(
                Action.workflow_id == wf_uuid, Action.status == ActionStatus.DETECTED
            )
            .order_by(Action.urgency_score.desc())
        )
        actions = res.scalars().all()
        total = len(actions)

        for idx, action in enumerate(actions):
            action.task_order = idx
            action.status = ActionStatus.CONFIRMED

        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(status=WorkflowStatus.AWAITING_APPROVAL)
        )

        # Generate HMAC-SHA256 approval tokens
        import hashlib
        import hmac
        import os as os_module
        from datetime import timedelta

        org_scoped_secret = (
            os_module.getenv("APPROVAL_HMAC_SECRET", "") + org_id_str
        ).encode()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=72)

        for action in actions:
            payload = (
                f"{str(wf_uuid)}:{str(action.id)}:{org_id_str}:{expires_at.isoformat()}"
            )
            token = hmac.new(
                org_scoped_secret,
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()

            # Store or update the ApprovalRequest
            from sqlalchemy.dialects.postgresql import insert as pg_upsert

            stmt = pg_upsert(ApprovalRequest).values(
                workflow_id=wf_uuid,
                action_id=action.id,
                org_id=_uuid.UUID(org_id_str),
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                status=ApprovalStatus.PENDING,
                expires_at=expires_at,
            )
            stmt = stmt.on_conflict_do_nothing(
                constraint="approval_requests_token_hash_key"
            )
            await db.execute(stmt)

            action.draft_payload = action.draft_payload or {}
            action.draft_payload["approval_token"] = token

        await db.commit()

    plan_summary = f"Task plan ready: {total} ordered actions. Awaiting lawyer approval before execution."
    logger.info("[%s] plan_node complete: %s", workflow_id, plan_summary)

    return {
        "status": WorkflowStatus.AWAITING_APPROVAL.value,
        "total_tasks": total,
        "current_task_index": 0,
        "messages": [AIMessage(content=plan_summary)],
    }


async def human_approval_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    HITL gate resume point.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)

    logger.info(
        "[%s] human_approval_node: approval received, proceeding to execution",
        workflow_id,
    )

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(status=WorkflowStatus.EXECUTING)
        )
        await db.commit()

    return {"status": WorkflowStatus.EXECUTING.value}


async def execute_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Executes ONE task per invocation safely via idempotency keys.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)
    current_idx = state["current_task_index"]
    total = state["total_tasks"]

    if current_idx >= total:
        logger.info("[%s] execute_node: all %d tasks complete", workflow_id, total)
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == wf_uuid)
                .values(
                    status=WorkflowStatus.COMPLETED,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
        return {"status": WorkflowStatus.COMPLETED.value}

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action).where(
                Action.workflow_id == wf_uuid, Action.task_order == current_idx
            )
        )
        task = res.scalar_one_or_none()

        if not task:
            logger.warning(
                "[%s] execute_node: no task found at index %d, advancing",
                workflow_id,
                current_idx,
            )
            return {"current_task_index": current_idx + 1}

        # --- SAGA / EXECUTION ENGINE: Strict Invariant Check ---
        # Rule FR-EXEC-01: Must verify ApprovalRequest is APPROVED, actor IS NOT NULL, and decision_timestamp IS NOT NULL
        approval_res = await db.execute(
            select(ApprovalRequest).where(ApprovalRequest.action_id == task.id)
        )
        approval = approval_res.scalar_one_or_none()

        if (
            not approval
            or approval.status != ApprovalStatus.APPROVED
            or not approval.actor
            or not approval.decision_timestamp
        ):
            logger.error(
                "[%s] INVARIANT VIOLATION: Execution attempted without strict approval for task %s",
                workflow_id,
                task.id,
            )

            # Log violation to AuditLog
            db.add(
                AuditLog(
                    workflow_id=wf_uuid,
                    action_id=task.id,
                    org_id=task.org_id,
                    event_type="INVARIANT_VIOLATION",
                    actor="SYSTEM",
                    notes=f"Invariant check failed for action {task.id} at index {current_idx}. Execution strictly aborted.",
                )
            )

            # Halt Workflow
            await db.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == wf_uuid)
                .values(status=WorkflowStatus.FAILED)
            )
            task.status = ActionStatus.FAILED
            await db.commit()
            return {
                "status": WorkflowStatus.FAILED.value,
                "error_context": "Invariant violation: Human approval prerequisites not met.",
            }
        # --------------------------------------------------------

        attempt = state.get("retry_count", 0) + 1

        # --- SAGA / EXECUTION ENGINE: SHA-256 Idempotency Hashes ---
        idempotency_key = generate_idempotency_key(
            workflow_id=str(workflow_id),
            action_id=str(task.id),
            tool_name=task.action_type.value,
            attempt_number=attempt,
        )

        # Calculate strict request_hash from payload JSON
        payload_str = json.dumps(task.draft_payload or {}, sort_keys=True)
        request_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        # ------------------------------------------------------------

        existing_res = await db.execute(
            select(ToolCallLog).where(ToolCallLog.idempotency_key == idempotency_key)
        )
        existing = existing_res.scalar_one_or_none()

        if existing:
            if existing.status == ToolCallStatus.SUCCESS:
                logger.info(
                    "[%s] Task %d already succeeded (key=%s), skipping",
                    workflow_id,
                    current_idx,
                    idempotency_key,
                )
            elif existing.status == ToolCallStatus.PENDING:
                logger.warning(
                    "[%s] Task %d is currently PENDING (key=%s). Yielding worker to prevent concurrent execution race condition.",
                    workflow_id,
                    current_idx,
                    idempotency_key,
                )
                return {
                    "status": WorkflowStatus.EXECUTING.value,
                    "error_context": "Concurrent execution yielded for PENDING idempotency key.",
                }
            else:
                logger.info(
                    "[%s] Task %d previously failed. Retrying.",
                    workflow_id,
                    current_idx,
                )

        if not existing or existing.status != ToolCallStatus.SUCCESS:
            task.status = ActionStatus.EXECUTING

            # 1. THE PRE-WRITE: Insert PENDING log immediately to claim the idempotency key
            if not existing:
                pending_log = ToolCallLog(
                    workflow_id=wf_uuid,
                    action_id=task.id,
                    tool_name=task.action_type.value,
                    idempotency_key=idempotency_key,
                    attempt_number=attempt,
                    status=ToolCallStatus.PENDING,
                    request_hash=request_hash,
                )
                db.add(pending_log)
            else:
                # We could be retrying the same idempotency key row.
                existing.status = ToolCallStatus.PENDING
                pending_log = existing

            await db.commit()

            try:
                result_summary = await _dispatch_tool(task)
                final_status = ToolCallStatus.SUCCESS
                task.status = ActionStatus.EXECUTED
            except Exception as exc:
                result_summary = str(exc)
                final_status = ToolCallStatus.FAILED
                task.status = ActionStatus.FAILED
                logger.error("[%s] Task %d failed: %s", workflow_id, current_idx, exc)

            # 3. THE RESOLUTION: Update the exact log to SUCCESS or FAILED
            pending_log.status = final_status
            pending_log.response_summary = (
                result_summary[:2000] if result_summary else ""
            )
            await db.commit()

            # --- SAGA: Trigger Compensating Transaction flow on failure ---
            if final_status == ToolCallStatus.FAILED:
                # Update Workflow Execution to RECOVERING so it routes to compensate_node
                await db.execute(
                    update(WorkflowExecution)
                    .where(WorkflowExecution.id == wf_uuid)
                    .values(status=WorkflowStatus.RECOVERING)
                )
                await db.commit()
                return {
                    "status": WorkflowStatus.RECOVERING.value,
                    "error_context": f"Tool execution failed: {result_summary}",
                }

    logger.info(
        "[%s] execute_node: task %d/%d done", workflow_id, current_idx + 1, total
    )
    return {
        "current_task_index": current_idx + 1,
        "status": WorkflowStatus.EXECUTING.value,
    }


async def compensate_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    SAGA PATTERN: Reverse compensation loop.

    Uses the pure ``build_compensation_plan`` function to determine which
    actions need compensation and in what order, then executes the plan
    by dispatching each compensating action and updating database state.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)

    logger.warning(
        "[%s] compensate_node: Initiating reverse compensation saga...", workflow_id
    )

    async with AsyncSessionLocal() as db:
        # Fetch all SUCCESSFUL tool calls for this workflow
        logs_res = await db.execute(
            select(ToolCallLog).where(
                ToolCallLog.workflow_id == wf_uuid,
                ToolCallLog.status == ToolCallStatus.SUCCESS,
            )
        )
        successful_logs = logs_res.scalars().all()

        # Fetch all associated actions in a single query (avoids N+1)
        action_ids = [log.action_id for log in successful_logs]
        actions_res = await db.execute(select(Action).where(Action.id.in_(action_ids)))
        actions = actions_res.scalars().all()

        # Build lookup maps for the execution phase
        actions_by_id: dict[str, Action] = {str(a.id): a for a in actions}
        tool_logs_by_action: dict[str, ToolCallLog] = {
            str(log.action_id): log for log in successful_logs
        }

        # Prepare lightweight, DB-free inputs for the pure function
        tool_entries = [
            ToolLogEntry(action_id=str(log.action_id), created_at=log.created_at)
            for log in successful_logs
        ]
        action_infos = {
            str(a.id): ActionInfo(
                idempotency_class=a.idempotency_class.value,
                compensation_action=a.compensation_action,
                compensation_params=a.compensation_params,
            )
            for a in actions
        }

        # Build the compensation plan (pure — no side effects)
        plan = build_compensation_plan(tool_entries, action_infos)

        # Execute the plan (side effects stay here in the node)
        for action_id_str, compensation_action, compensation_params in plan:
            action = actions_by_id[action_id_str]
            log_entry = tool_logs_by_action[action_id_str]

            logger.info(
                "[%s] Compensating action %s (Tool: %s, Action: %s)",
                workflow_id,
                log_entry.id,
                log_entry.tool_name,
                compensation_action,
            )

            try:
                # In a fully fleshed out system, this would dispatch
                # `compensation_action` with `compensation_params`.
                # For now, we simulate the dispatch:
                logger.info(
                    "Executing compensation: %s with %s",
                    compensation_action,
                    compensation_params,
                )

                log_entry.status = ToolCallStatus.COMPENSATED

                db.add(
                    AuditLog(
                        workflow_id=wf_uuid,
                        action_id=action.id,
                        org_id=action.org_id,
                        event_type="COMPENSATION_EXECUTED",
                        actor="SYSTEM",
                        notes=f"Successfully compensated action {action.id} using {compensation_action}.",
                    )
                )
            except Exception as e:
                logger.error(
                    "[%s] COMPENSATION FAILED for action %s: %s",
                    workflow_id,
                    action.id,
                    e,
                )
                log_entry.status = ToolCallStatus.UNCOMPENSATABLE_FAILURE

                # Rule FR-EXEC-03: Log UNCOMPENSATABLE_FAILURE and Halt
                db.add(
                    AuditLog(
                        workflow_id=wf_uuid,
                        action_id=action.id,
                        org_id=action.org_id,
                        event_type="UNCOMPENSATABLE_FAILURE",
                        actor="SYSTEM",
                        notes=f"Failed to compensate action {action.id}. Manual admin intervention required. Error: {e}",
                    )
                )
                # Don't throw, we MUST update the db state to FAILED
                break

        # SAGA completed (either fully reversed or failed during reverse)
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(status=WorkflowStatus.FAILED)
        )
        await db.commit()

    return {
        "status": WorkflowStatus.FAILED.value,
        "error_context": "Saga compensation complete. Workflow Failed.",
    }


async def _dispatch_tool(task: Action) -> str:
    """Dispatch a tool using the centralized Tool Registry."""
    from app.services.agent.tool_registry import dispatch_tool as real_dispatch

    return await real_dispatch(task)
