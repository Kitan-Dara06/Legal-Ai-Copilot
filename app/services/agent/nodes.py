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
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

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


# No-op decorator — node_tracer was removed with LangGraph
def traced_node(func):
    return func


from app.services.notifications import notify_approval_needed
from app.services.object_storage import upload_bytes
from app.utils import sanitize_goal_text
from langchain_core.messages import AIMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

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
        "session_file_ids": [],
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


@traced_node
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


@traced_node
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


@traced_node
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

    # ── Resolve session file IDs to filename for scoped search ──
    session_specific_contracts = None
    session_file_ids = state.get("session_file_ids", [])
    if session_file_ids and all(isinstance(fid, str) for fid in session_file_ids):
        try:
            from app.database import AsyncSessionLocal
            from app.models import Document
            from sqlalchemy import select

            async with AsyncSessionLocal() as lookup_db:
                doc_res = await lookup_db.execute(
                    select(Document.filename).where(
                        Document.id.in_([_uuid.UUID(fid) for fid in session_file_ids])
                    )
                )
                filenames = [row[0] for row in doc_res.all()]
                if filenames:
                    session_specific_contracts = filenames
                    logger.info(
                        "[%s] Scoping REASON search to %d document(s)",
                        workflow_id,
                        len(filenames),
                    )
        except Exception as doc_err:
            logger.warning(
                "[%s] Could not resolve session docs: %s", workflow_id, doc_err
            )

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
            specific_contracts=session_specific_contracts,
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


class DraftResult(BaseModel):
    """
    Structured output for the ACT path draft generation.
    Mirrors AnalyzeResult to enforce strict chunk anchoring.
    """

    draft_text: str = Field(
        ...,
        description="The consolidated professional notice addressing all obligations",
    )
    source_citations: list[str] = Field(
        ...,
        description=(
            "List of exact source text excerpts cited in the draft, "
            "each with document name and section reference"
        ),
    )
    grounding_score: float = Field(
        ...,
        description=(
            "0.0 to 1.0 — how strictly the draft is grounded in the provided source texts. "
            "1.0 = every claim maps to a verbatim source quote"
        ),
    )
    missing_info: list[str] = Field(
        default_factory=list,
        description=(
            "List of information gaps where the source text was insufficient "
            "to complete a required section of the notice"
        ),
    )


@traced_node
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


@traced_node
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


@traced_node
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


@traced_node
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


@traced_node
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


@traced_node
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


@traced_node
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


@traced_node
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

        # ── Deadline-based actions with proper filtering ──────────────
        #  1) Raised threshold: only urgency >= 0.7
        deadlines_res = await db.execute(
            select(DeadlineRegistry)
            .where(
                DeadlineRegistry.workspace_id == workspace_id,
                DeadlineRegistry.urgency_score >= 0.7,
            )
            .order_by(DeadlineRegistry.urgency_score.desc())
        )
        deadlines = deadlines_res.scalars().all()

        #  2) Filter out dormant / standard obligations:
        #     If no resolved deadline OR deadline > 90 days out, skip
        #     unless urgency >= 0.8.
        now_utc = datetime.now(timezone.utc)
        cutoff_date = now_utc + timedelta(days=90)
        filtered_deadlines: list = []
        for dl in deadlines:
            score = float(dl.urgency_score)
            has_resolved = dl.resolved_deadline is not None
            deadline_soon = has_resolved and dl.resolved_deadline <= cutoff_date
            if deadline_soon or score >= 0.8:
                filtered_deadlines.append(dl)

        #  3) Deduplicate near-identical obligation descriptions.
        #     Two descriptions sharing > 80% of words → keep higher urgency.
        def _word_set(text: str) -> set:
            return set(text.lower().split())

        deduped_deadlines: list = []
        for dl in filtered_deadlines:
            dl_words = _word_set(dl.obligation_description)
            is_dup = False
            for i, kept in enumerate(deduped_deadlines):
                kept_words = _word_set(kept.obligation_description)
                union = dl_words | kept_words
                if not union:
                    continue
                overlap = len(dl_words & kept_words) / len(union)
                if overlap > 0.80:
                    # Keep whichever has higher urgency (list is pre-sorted
                    # descending, so `kept` is >= `dl` already).
                    is_dup = True
                    break
            if not is_dup:
                deduped_deadlines.append(dl)

        #  4) Cap at 10 actions maximum (already sorted by urgency desc).
        MAX_DEADLINE_ACTIONS = 10
        capped_deadlines = deduped_deadlines[:MAX_DEADLINE_ACTIONS]

        for dl in capped_deadlines:
            source_ref = dl.source_clause_a
            # ── Quality gate: skip if source text is missing or invalid ──
            source_text = (source_ref or {}).get("text", "")
            source_doc_id = (source_ref or {}).get("document_id", "")
            if not source_text or not source_doc_id or source_doc_id == "unknown":
                logger.warning(
                    "[%s] Skipping deadline %s: missing source text or document_id",
                    workflow_id,
                    dl.id,
                )
                continue

            # ── Session scoping: skip if document not in user's selection ──
            session_file_ids = state.get("session_file_ids", [])
            if session_file_ids and source_doc_id not in session_file_ids:
                logger.info(
                    "[%s] Skipping deadline %s: document %s not in session",
                    workflow_id,
                    dl.id,
                    source_doc_id[:8],
                )
                continue
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

        logger.info(
            "[%s] detect_node deadline filtering: %d raw -> %d after dormant filter "
            "-> %d after dedup -> %d after cap",
            workflow_id,
            len(deadlines),
            len(filtered_deadlines),
            len(deduped_deadlines),
            len(capped_deadlines),
        )

        # Status update only — no Action rows written yet (deferred to draft_node)
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(status=WorkflowStatus.BRIEFING)
        )
        await db.commit()

    action_count = len(detected)
    summary = (
        f"Detected {action_count} candidate actions: {len(conflicts)} definitional conflicts, "
        f"{len(capped_deadlines)} deadline obligations "
        f"(filtered from {len(deadlines)} raw candidates). Awaiting brief confirmation."
    )
    logger.info("[%s] detect_node complete: %s", workflow_id, summary)

    # Return raw data for decision_brief_node — NOT persisted Action rows
    conflict_dicts = [
        {
            "term": c.term,
            "definition": c.definition,
            "clause_reference": c.clause_reference,
            "conflict_description": c.conflict_description,
        }
        for c in conflicts
    ]
    deadline_dicts = [
        {
            "id": str(dl.id),
            "obligation_description": dl.obligation_description,
            "obligation_type": dl.obligation_type.value,
            "urgency_score": float(dl.urgency_score),
            "resolved_deadline": dl.resolved_deadline.isoformat()
            if dl.resolved_deadline
            else None,
            "source_clause_a": dl.source_clause_a,
        }
        for dl in capped_deadlines
    ]

    return {
        "status": WorkflowStatus.BRIEFING.value,
        "findings_summary": summary,
        "action_count": action_count,
        "_detected_conflicts": conflict_dicts,
        "_detected_deadlines": deadline_dicts,
    }


# ── Decision Brief schemas (locked from test_decision_brief.py validation) ────
from datetime import date as _date


class _ActionRecommendation(BaseModel):
    action_type: str
    description: str
    urgency: float = Field(ge=0.0, le=1.0)
    deadline_date: Optional[_date] = None
    deadline_source: Optional[str] = None


class _RelevantClause(BaseModel):
    clause_ref: str
    excerpt: str
    relevance_reason: str
    document_id: str


class _ConflictFlag(BaseModel):
    type: str
    description: str
    source_a: str
    source_b: Optional[str] = None


class DecisionBriefResult(BaseModel):
    goal: str
    summary: str
    recommended_actions: list[_ActionRecommendation]
    relevant_clauses: list[_RelevantClause]
    conflicts_to_resolve: list[_ConflictFlag]
    deadlines_implicated: list[dict]
    verification_checklist: list[str]
    proceed_recommended: bool
    proceed_reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


@traced_node
async def decision_brief_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    ACT Phase 1: Generates a Decision Brief for the lawyer.

    Synthesises retrieved contract context + raw detect output (conflicts,
    deadlines) into a structured DecisionBriefResult.  The result is
    persisted to workflow_executions.decision_brief_payload and the
    workflow is paused at AWAITING_BRIEF_CONFIRMATION (HITL 1).

    Action rows are NOT created here — deferred to draft_node after
    lawyer confirms the brief.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)
    goal_text = state.get("goal_text", "")
    org_id = state.get("org_id", "")
    workspace_id = state.get("workspace_id", "")
    context_text = state.get("context_text", "")
    detected_conflicts = state.get("_detected_conflicts") or []
    detected_deadlines = state.get("_detected_deadlines") or []

    logger.info("[%s] decision_brief_node: generating brief", workflow_id)

    # ── Build context block from retrieved context_text ─────────────────────
    # context_text is already formatted by retrieval_node as numbered chunks
    context_block = context_text if context_text else "[No retrieved context available]"

    # Augment with registry findings from detect_node
    registry_section = ""
    if detected_conflicts:
        registry_section += "\n\n=== DETECTED DEFINITIONAL CONFLICTS ===\n"
        for c in detected_conflicts:
            registry_section += (
                f"- Term: {c.get('term')} | Ref: {c.get('clause_reference')}\n"
                f"  Conflict: {c.get('conflict_description', 'N/A')}\n"
            )
    if detected_deadlines:
        registry_section += "\n\n=== DETECTED DEADLINES (urgency ≥ 0.7) ===\n"
        for d in detected_deadlines:
            registry_section += (
                f"- {d.get('obligation_description')} "
                f"[urgency={d.get('urgency_score'):.2f}, "
                f"deadline={d.get('resolved_deadline') or 'unresolved'}]\n"
            )

    # ── Prompt (validated in scripts/test_decision_brief.py) ────────────────
    prompt = f"""You are a senior legal AI assistant generating a Decision Brief for a lawyer.

The lawyer needs to decide: "Should I proceed with drafting a response, and if so, what exactly should I draft?"

Your task is to synthesize the retrieved contract context into a structured brief that:
1. Identifies the SPECIFIC clauses most relevant to the goal (not everything — just what matters)
2. Recommends concrete actions with their action type (DRAFT_RESPONSE, DRAFT_NOTICE, or DRAFT_AMENDMENT)
3. Flags any conflicts or ambiguities the lawyer must resolve before drafting
4. Identifies implicated deadlines
5. Produces a verification checklist of things the lawyer MUST confirm before a draft is generated
6. Gives your own assessment of whether to proceed and why

STRICT RULES:
- relevant_clauses MUST only contain clauses from the provided context chunks — no hallucination
- excerpt MUST be a verbatim quote from the chunk text — never paraphrased
- If the context is insufficient to form a brief, set proceed_recommended=false and explain in proceed_reasoning
- PREMISE CONFLICT RULE: If the retrieved context reveals that the goal references a wrong clause number, a non-existent provision, or a misidentified clause type, you MUST set proceed_recommended=false. The summary must lead with this conflict as sentence one — do not bury it. Do not recommend drafting on a faulty premise.
- relevance_reason must explain specifically how this clause affects the drafting strategy — not just that the topic appears in it.
- verification_checklist items must be specific and actionable — not generic platitudes like "review the contract"
- summary must be 2-3 sentences maximum. Lead with the most important finding. Do not hedge. Write as if briefing a senior partner who has 20 seconds to read it.
- If no deadlines are identified, deadlines_implicated must be an empty list [], not a list containing null objects.

GOAL: {goal_text}

RETRIEVED CONTRACT CONTEXT:
{context_block}{registry_section}

Generate the DecisionBriefResult now."""

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", ""),
    ).with_structured_output(DecisionBriefResult)

    brief: DecisionBriefResult = await llm.ainvoke(prompt)

    # ── Persist brief and pause ──────────────────────────────────────────────
    brief_payload = brief.model_dump(mode="json")
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(
                decision_brief_payload=brief_payload,
                status=WorkflowStatus.AWAITING_BRIEF_CONFIRMATION,
            )
        )
        await db.commit()

    logger.info(
        "[%s] decision_brief_node: brief generated (confidence=%.2f, proceed=%s)",
        workflow_id,
        brief.confidence,
        brief.proceed_recommended,
    )

    return {
        "status": WorkflowStatus.AWAITING_BRIEF_CONFIRMATION.value,
        "findings_summary": brief.summary,
    }


def _compress_clause(text: str, max_chars: int = 600) -> str:
    """
    Compress a clause to its essential content.
    - Extracts sentences containing key legal terms first
    - Falls back to truncated head if no key sentences found
    - Never exceeds max_chars to keep prompts lean
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    legal_keywords = {
        "shall",
        "must",
        "agree",
        "obligation",
        "party",
        "parties",
        "section",
        "clause",
        "term",
        "notice",
        "waiver",
        "indemnify",
        "breach",
        "terminate",
        "liability",
        "represent",
        "warrant",
        "covenant",
        "default",
        "remedy",
        "assign",
        "govern",
    }
    sentences = text.replace("\n", " ").split(". ")
    priority_sentences = []
    remaining_chars = max_chars
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        words = set(w.lower() for w in sent.split())
        if words & legal_keywords:
            if len(sent) + 3 <= remaining_chars:
                priority_sentences.append(sent)
                remaining_chars -= len(sent) + 2
            else:
                break

    if priority_sentences:
        result = ". ".join(priority_sentences) + "."
        if remaining_chars < 100:
            if len(result) > max_chars:
                result = result[:max_chars] + " [...]"
        return result

    # No legal keywords found - just take the head
    return text[:max_chars] + " [...]"


def _extract_source_text(source_clause_ref: Optional[Dict]) -> str:
    """
    Extract the actual clause text from source_clause_ref JSONB.
    The structure is: {"document_id": "...", "hierarchy": [...], "text": "..."}
    Returns the text content or an empty string if unavailable.
    """
    if not source_clause_ref:
        return ""
    return source_clause_ref.get("text", "") or ""


def _extract_hierarchy(source_clause_ref: Optional[Dict]) -> str:
    """
    Extract the section hierarchy from source_clause_ref for citation purposes.
    Returns a human-readable section path or empty string.
    """
    if not source_clause_ref or not isinstance(source_clause_ref, dict):
        return ""
    hierarchy = source_clause_ref.get("hierarchy", [])
    if isinstance(hierarchy, list) and hierarchy:
        return " > ".join(str(h) for h in hierarchy)
    return ""


async def _retrieve_additional_context(
    obligation_description: str,
    org_id: str,
    workspace_id: str,
    document_id: Optional[str] = None,
    top_k: int = 3,
) -> str:
    """
    Retrieve additional related chunks from the vector store to give the
    drafter broader contract context beyond the single source clause.

    When document_id is provided, scopes the search to that specific document
    (by looking up the filename and using it as a filter) to prevent pulling
    clauses from unrelated contracts in the same workspace.
    """
    try:
        from app.services.ingestion.embedder import LegalEmbedder
        from app.services.store import search_hybrid

        embedder = LegalEmbedder()
        query_vec = embedder.get_voyage_query_vector(obligation_description)

        # ── Resolve document filename for scoped search ──
        specific_contract = None
        if document_id:
            try:
                from app.database import AsyncSessionLocal
                from app.models import Document
                from sqlalchemy import select

                async with AsyncSessionLocal() as lookup_db:
                    doc_res = await lookup_db.execute(
                        select(Document.filename).where(
                            Document.id == _uuid.UUID(document_id)
                        )
                    )
                    filename = doc_res.scalar_one_or_none()
                    if filename:
                        specific_contract = filename
                        logger.info(
                            "Scoping additional context to document '%s' (%s)",
                            filename,
                            document_id,
                        )
            except Exception as doc_err:
                logger.warning(
                    "Could not resolve document_id=%s for scoped search: %s",
                    document_id,
                    doc_err,
                )
                return ""  # Don't fall through to unscoped search

        search_response = search_hybrid(
            query_text=obligation_description,
            query_vector=query_vec,
            top_k=top_k,
            org_id=org_id,
            workspace_id=workspace_id,
            specific_contract=specific_contract,
        )

        results = (
            search_response.get("results", [])
            if isinstance(search_response, dict)
            else search_response
        )

        context_parts = []
        for r in results:
            text = r.get("text", "")
            source = r.get("metadata", {}).get("source", "Unknown")
            page = r.get("metadata", {}).get("page", "?")
            result_file_id = r.get("metadata", {}).get("file_id")
            if text:
                # If we know the document filename, label it clearly
                if specific_contract:
                    source = specific_contract
                context_parts.append(f"[Source: {source}, Page {page}]\n{text}")

        return "\n\n---\n\n".join(context_parts)
    except Exception as e:
        logger.warning("Failed to retrieve additional context: %s", e)
        return ""


@traced_node
async def draft_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    ACT Phase 2: Generates grounded drafts from the confirmed Decision Brief.

    Reads DecisionBriefResult from workflow_executions.decision_brief_payload,
    creates Action rows (first time — lawyer has confirmed the brief), then
    generates one consolidated draft grounded against relevant_clauses only.

    Pauses at AWAITING_APPROVAL (HITL 2) for lawyer review.
    export_node filters on ActionStatus.DRAFTING to find the draft.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)
    goal_text = state.get("goal_text", "")
    org_id = _uuid.UUID(state["org_id"])
    workspace_id = _uuid.UUID(state["workspace_id"])

    logger.info("[%s] draft_node: reading brief and generating draft", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.DRAFTING)

    # ── Read the confirmed brief ─────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        wf_res = await db.execute(
            select(WorkflowExecution).where(WorkflowExecution.id == wf_uuid)
        )
        wf = wf_res.scalar_one_or_none()

    if not wf or not wf.decision_brief_payload:
        logger.error("[%s] draft_node: no decision_brief_payload found", workflow_id)
        return {
            "status": WorkflowStatus.FAILED.value,
            "error_context": "Brief payload missing",
        }

    brief = DecisionBriefResult(**wf.decision_brief_payload)

    # ── Create Action rows now (brief confirmed by lawyer) ───────────────────
    action_rows = []
    for rec in brief.recommended_actions:
        try:
            action_type = (
                ActionType[rec.action_type]
                if rec.action_type in ActionType.__members__
                else ActionType.DRAFT_RESPONSE
            )
        except (KeyError, AttributeError):
            action_type = ActionType.DRAFT_RESPONSE

        action_rows.append(
            dict(
                id=_uuid.uuid4(),
                workflow_id=wf_uuid,
                org_id=org_id,
                action_type=action_type,
                description=rec.description,
                urgency_score=rec.urgency,
                status=ActionStatus.CONFIRMED,
                task_order=0,
            )
        )

    async with AsyncSessionLocal() as db:
        if action_rows:
            stmt = pg_insert(Action).values(action_rows).on_conflict_do_nothing()
            await db.execute(stmt)
        await db.commit()

    # ── Build grounding context from relevant_clauses ────────────────────────
    clause_block = ""
    for i, clause in enumerate(brief.relevant_clauses, 1):
        clause_block += (
            f"[Clause {i}] Ref: {clause.clause_ref} | Doc: {clause.document_id}\n"
            f"Relevance: {clause.relevance_reason}\n"
            f'Text: "{clause.excerpt}"\n\n'
        )

    checklist_block = "\n".join(
        f"{i}. {item}" for i, item in enumerate(brief.verification_checklist, 1)
    )

    prompt = (
        f"You are an expert legal AI assistant.\n"
        f"The lawyer has reviewed the Decision Brief and confirmed: proceed with drafting.\n\n"
        f"GOAL: {goal_text}\n\n"
        f"BRIEF SUMMARY: {brief.summary}\n\n"
        f"=== GROUNDING CLAUSES (draft ONLY from these) ===\n{clause_block}\n"
        f"=== VERIFICATION CHECKLIST ===\n{checklist_block}\n\n"
        f"RULES:\n"
        f"- Draft ONLY from the grounding clauses above — no hallucination\n"
        f"- Every factual claim must map to a specific excerpt\n"
        f"- If information is missing, say so explicitly rather than inventing it\n"
        f"- Address every item in the verification checklist in the draft\n\n"
        f"Output format (JSON):\n"
        f"- draft_text: the full professional draft\n"
        f"- source_citations: list of verbatim excerpts cited\n"
        f"- grounding_score: 0.0–1.0\n"
        f"- missing_info: list of gaps not covered by the source clauses"
    )

    try:
        llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            api_key=os.getenv("GROQ_API_KEY", ""),
            max_tokens=4096,
        )

        class DraftOutput(BaseModel):
            draft_text: str = Field(description="The full professional draft")
            source_citations: list[str] = Field(
                description="Verbatim excerpts from grounding clauses cited in the draft"
            )
            grounding_score: float = Field(
                ge=0.0,
                le=1.0,
                description="Fraction of claims traceable to a grounding clause",
            )
            missing_info: list[str] = Field(
                description="Information gaps not covered by the source clauses"
            )

        structured_llm = llm.with_structured_output(DraftOutput)
        draft_output: DraftOutput = await structured_llm.ainvoke(prompt)

        draft_text = draft_output.draft_text
        source_citations = draft_output.source_citations
        grounding_score = draft_output.grounding_score
        missing_info_list = draft_output.missing_info

    except Exception as draft_err:
        logger.error("[%s] draft_node LLM call failed: %s", workflow_id, draft_err)
        return {
            "status": WorkflowStatus.FAILED.value,
            "error_context": f"Draft generation failed: {draft_err}",
        }

    # Write draft payload to the first Action row
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action)
            .where(Action.workflow_id == wf_uuid)
            .order_by(Action.task_order)
            .limit(1)
        )
        primary_action = res.scalar_one_or_none()
        if primary_action:
            primary_action.draft_payload = {
                "draft_text": draft_text,
                "source_citations": source_citations,
                "grounding_score": grounding_score,
                "missing_info": missing_info_list,
                "verification_checklist": brief.verification_checklist,
            }
            primary_action.status = ActionStatus.AWAITING_APPROVAL
        await db.commit()

    await _set_wf_status(workflow_id, WorkflowStatus.AWAITING_APPROVAL)
    logger.info(
        "[%s] draft_node complete: grounding=%.2f, %d citations",
        workflow_id,
        grounding_score,
        len(source_citations),
    )
    return {"status": WorkflowStatus.AWAITING_APPROVAL.value}


@traced_node
async def export_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    ACT Phase 3: Exports the approved draft as DOCX and uploads to R2.

    Reads the approved Action row, builds a DOCX via python-docx,
    uploads to R2 under drafts/{workflow_id}/{action_id}.docx,
    stores the R2 key in draft_r2_key state field, and marks
    WorkflowStatus.COMPLETED.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    wf_uuid = _uuid.UUID(workflow_id)
    org_id = _uuid.UUID(state["org_id"])

    logger.info("[%s] export_node: building DOCX", workflow_id)

    # Read the approved Action
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action)
            .where(
                Action.workflow_id == wf_uuid,
                Action.status == ActionStatus.AWAITING_APPROVAL,
            )
            .order_by(Action.task_order)
            .limit(1)
        )
        action = res.scalar_one_or_none()

    if not action or not action.draft_payload:
        logger.error(
            "[%s] export_node: no approved action with draft payload", workflow_id
        )
        return {
            "status": WorkflowStatus.FAILED.value,
            "error_context": "No approved draft found for export",
        }

    draft_text: str = action.draft_payload.get("draft_text", "")
    citations: list = action.draft_payload.get("source_citations", [])
    checklist: list = action.draft_payload.get("verification_checklist", [])

    # Build DOCX
    try:
        import io

        from docx import Document as DocxDocument
        from docx.shared import Pt

        doc = DocxDocument()
        doc.add_heading("Legal Draft", level=1)
        doc.add_paragraph(draft_text)

        if citations:
            doc.add_heading("Source Citations", level=2)
            for cite in citations:
                p = doc.add_paragraph(style="List Bullet")
                p.add_run(cite)

        if checklist:
            doc.add_heading("Verification Checklist", level=2)
            for i, item in enumerate(checklist, 1):
                p = doc.add_paragraph(style="List Number")
                p.add_run(item)

        buf = io.BytesIO()
        doc.save(buf)
        docx_bytes = buf.getvalue()

    except Exception as docx_err:
        logger.error("[%s] export_node DOCX build failed: %s", workflow_id, docx_err)
        return {
            "status": WorkflowStatus.FAILED.value,
            "error_context": f"DOCX build failed: {docx_err}",
        }

    # Upload to R2
    r2_key = f"drafts/{workflow_id}/{action.id}.docx"
    try:
        await upload_bytes(
            key=r2_key,
            data=docx_bytes,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as upload_err:
        logger.error("[%s] export_node R2 upload failed: %s", workflow_id, upload_err)
        return {
            "status": WorkflowStatus.FAILED.value,
            "error_context": f"R2 upload failed: {upload_err}",
        }

    # Mark action as EXECUTED and workflow COMPLETED
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Action)
            .where(Action.id == action.id)
            .values(status=ActionStatus.EXECUTED)
        )
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id == wf_uuid)
            .values(
                status=WorkflowStatus.COMPLETED,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    logger.info("[%s] export_node: uploaded DOCX to %s", workflow_id, r2_key)
    return {
        "status": WorkflowStatus.COMPLETED.value,
        "draft_r2_key": r2_key,
    }


# =============================================================================
# REMOVED: This node is no longer part of the ACT path (replaced by the
# Decision Brief -> Draft -> Export pipeline). Kept for reference only.
# DO NOT wire into graph.py.
# =============================================================================
