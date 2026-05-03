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
import os
import json
import logging
import uuid as _uuid
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
    DeadlineRegistry,
    DefinedTermRegistry,
    ToolCallLog,
    ToolCallStatus,
    WorkflowExecution,
    WorkflowStatus,
)
from app.services.agent.agent_state import CURRENT_GRAPH_VERSION, PointerOnlyState
from app.utils import sanitize_goal_text

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# State adapter — backward compatibility across graph versions
# ─────────────────────────────────────────────────────────────────────────────
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
    primary_intent: str = Field(..., description="Must be 'ANALYZE', 'REASON', or 'ACT'")
    confidence: float = Field(..., description="0.0 to 1.0 confidence score")
    reasoning: str = Field(..., description="One sentence explaining the classification")
    requires_graph: bool = Field(..., description="Whether cross-document reasoning is needed")
    requires_action: bool = Field(..., description="Whether execution is implied")
    deadline_sensitive: bool = Field(..., description="Whether a deadline was detected in the goal")
    urgency_score: float = Field(..., description="0.0 to 1.0 urgency score based on deadline proximity")


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
        api_key=os.getenv("GROQ_API_KEY", "")
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
            primary_intent="ANALYZE", confidence=0.5, reasoning="Fallback due to LLM error",
            requires_graph=False, requires_action=False, deadline_sensitive=False, urgency_score=0.0
        )

    logger.info("[%s] Intent classified as %s (confidence: %.2f)", workflow_id, classification.primary_intent, classification.confidence)

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
    
    logger.info("[%s] ambiguity_gate_node: Pausing for user confirmation (confidence=%.2f)", workflow_id, state["intent_confidence"])
    await _set_wf_status(workflow_id, WorkflowStatus.AWAITING_INTENT_CONFIRMATION)

    return {"status": WorkflowStatus.AWAITING_INTENT_CONFIRMATION.value}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2A: ANALYZE / REASON PATH NODES
# ─────────────────────────────────────────────────────────────────────────────

async def retrieval_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Executes hybrid search (Qdrant).
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = state.get("goal_text", "")
    org_id = state.get("org_id")
    
    logger.info("[%s] retrieval_node: Executing hybrid vector search...", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.RETRIEVING)
    
    from app.services.store import search_hybrid
    from app.services.ingestion.embedder import LegalEmbedder
    
    try:
        # Embed the query
        embedder = LegalEmbedder()
        query_vector = embedder.get_voyage_query_vector(goal_text)
        
        # We search across the whole org since we don't have filename directly
        results = search_hybrid(
            query_text=goal_text,
            query_vector=query_vector,
            top_k=5,
            org_id=org_id
        )
        
        # Format the retrieved chunks into a context string
        context_parts = []
        for r in results:
            text = r.get("text", "")
            source = r.get("metadata", {}).get("source", "Unknown Document")
            page = r.get("metadata", {}).get("page", "?")
            context_parts.append(f"Source: {source} (Page {page})\n{text}")
            
        context_text = "\n\n---\n\n".join(context_parts)
        if not context_text:
            context_text = "No relevant context found in the documents."
            
    except Exception as e:
        logger.error("[%s] Qdrant retrieval failed: %s", workflow_id, e)
        context_text = "Error retrieving context from database."

    return {
        "status": WorkflowStatus.RETRIEVING.value,
        "context_text": context_text
    }


async def synthesis_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Generates a cited answer for the ANALYZE path using Groq.
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = sanitize_goal_text(state.get("goal_text", ""))
    context_text = state.get("context_text", "No context provided.")
    
    logger.info("[%s] synthesis_node: Synthesizing answer...", workflow_id)
    
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", "")
    )
    
    prompt = (
        f"You are an expert legal AI assistant. Your task is to answer the user's question based strictly on the provided legal text context.\n\n"
        f"USER QUESTION: {goal_text}\n\n"
        f"DOCUMENT CONTEXT:\n{context_text}\n\n"
        f"Please provide a clear, professional summary. Cite your sources using the document names provided in the context."
    )
    
    try:
        response = await llm.ainvoke(prompt)
        findings = response.content
    except Exception as e:
        logger.error("[%s] synthesis_node failed: %s", workflow_id, e)
        findings = "Error: Failed to synthesize answer using LLM."

    await _set_wf_status(workflow_id, WorkflowStatus.COMPLETED)
    return {
        "status": WorkflowStatus.COMPLETED.value,
        "findings_summary": findings,
    }


async def graph_expansion_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Queries Neo4j for cross-references to expand retrieved chunks (REASON path).
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = state.get("goal_text", "")
    org_id = state.get("org_id")
    
    logger.info("[%s] graph_expansion_node: Expanding graph references...", workflow_id)
    await _set_wf_status(workflow_id, WorkflowStatus.EXPANDING)

    from app.services.store import search_hybrid
    from app.services.ingestion.embedder import LegalEmbedder
    from app.services.ingestion.graph_extractor import DependencyGraph

    try:
        # 1. Get base chunks
        embedder = LegalEmbedder()
        query_vector = embedder.get_voyage_query_vector(goal_text)
        results = search_hybrid(
            query_text=goal_text,
            query_vector=query_vector,
            top_k=3, 
            org_id=org_id
        )

        # 2. Expand via Neo4j
        graph = DependencyGraph()
        expansion_parts = []
        seen_nodes = set()

        for r in results:
            ref = r.get("metadata", {}).get("clause_reference")
            if not ref: continue
            
            chains = graph.get_dependency_chains(node_id=ref, max_hops=3)
            for c in chains:
                node_id = c.get("node")
                if node_id in seen_nodes: continue
                seen_nodes.add(node_id)
                
                text = c.get("text", "")
                source = c.get("source_document", "Unknown")
                expansion_parts.append(f"Expanded Ref: {node_id} (Source: {source})\n{text}")

        graph.close()

        # 3. Merge with existing context
        context_text = state.get("context_text", "")
        if expansion_parts:
            context_text += "\n\n=== CROSS-DOCUMENT GRAPH EXPANSION ===\n\n"
            context_text += "\n\n----- \n\n".join(expansion_parts)

    except Exception as e:
        logger.error("[%s] Graph expansion failed: %s", workflow_id, e)
        context_text = state.get("context_text", "")

    return {
        "status": WorkflowStatus.EXPANDING.value,
        "context_text": context_text
    }


async def findings_node(state: PointerOnlyState) -> Dict[str, Any]:
    """
    Generates structured findings and typed escalations for Due Diligence (REASON path).
    """
    state = _adapt_state(state)
    workflow_id = state["workflow_id"]
    goal_text = sanitize_goal_text(state.get("goal_text", ""))
    context_text = state.get("context_text", "No context provided.")
    
    logger.info("[%s] findings_node: Generating due diligence report...", workflow_id)
    
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", "")
    )
    
    prompt = (
        f"You are an expert legal due diligence auditor. Your task is to analyze the provided contract context (including cross-references) "
        f"to answer the following query. Focus on identifying conflicts, dependencies, or hidden obligations across documents.\n\n"
        f"USER QUERY: {goal_text}\n\n"
        f"CONTEXT (BASE + EXPANDED):\n{context_text}\n\n"
        f"Please provide a structured report with 'Findings', 'Cross-Document Dependencies', and 'Risk Assessment'."
    )
    
    try:
        response = await llm.ainvoke(prompt)
        findings = response.content
    except Exception as e:
        logger.error("[%s] findings_node failed: %s", workflow_id, e)
        findings = "Error: Failed to generate due diligence report."

    await _set_wf_status(workflow_id, WorkflowStatus.COMPLETED)
    return {
        "status": WorkflowStatus.COMPLETED.value,
        "findings_summary": findings,
    }


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
            detected.append(dict(
                workflow_id=wf_uuid,
                org_id=org_id,
                action_type=ActionType.FLAG_FOR_REVIEW if hasattr(ActionType, "FLAG_FOR_REVIEW") else ActionType.ESCALATE_TO_COUNSEL,
                description=f"Resolve definitional conflict for term '{c.term}'",
                urgency_score=0.7,
                status=ActionStatus.DETECTED,
                task_order=0,
            ))

        deadlines_res = await db.execute(
            select(DeadlineRegistry).where(
                DeadlineRegistry.workspace_id == workspace_id,
                DeadlineRegistry.urgency_score >= 0.5,
            ).order_by(DeadlineRegistry.urgency_score.desc())
        )
        deadlines = deadlines_res.scalars().all()
        for dl in deadlines:
            source_ref = dl.source_clause_a
            detected.append(dict(
                workflow_id=wf_uuid,
                org_id=org_id,
                action_type=ActionType.SEND_NOTICE,
                description=f"Address obligation: {dl.obligation_description}",
                urgency_score=float(dl.urgency_score),
                status=ActionStatus.DETECTED,
                source_clause_ref=source_ref,
                deadline_id=dl.id,
                task_order=0,
            ))

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
        api_key=os.getenv("GROQ_API_KEY", "")
    )

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action).where(Action.workflow_id == wf_uuid, Action.status == ActionStatus.DETECTED)
        )
        actions = res.scalars().all()

        for action in actions:
            prompt = (
                f"You are a legal AI assistant. Please draft the content for the following legal action:\n"
                f"Action Type: {action.action_type.value}\n"
                f"Description: {action.description}\n"
                f"Context Reference: {action.source_clause_ref}\n\n"
                f"Draft a professional and legally appropriate response or notice."
            )
            try:
                response = await llm.ainvoke(prompt)
                action.draft_payload = {"draft_text": response.content}
                logger.info("[%s] Drafted content for action %s", workflow_id, action.id)
            except Exception as e:
                logger.error("[%s] Drafting failed for action %s: %s", workflow_id, action.id, e)
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
            .where(Action.workflow_id == wf_uuid, Action.status == ActionStatus.DETECTED)
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

    logger.info("[%s] human_approval_node: approval received, proceeding to execution", workflow_id)

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
                .values(status=WorkflowStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            )
            await db.commit()
        return {"status": WorkflowStatus.COMPLETED.value}

    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(Action).where(Action.workflow_id == wf_uuid, Action.task_order == current_idx)
        )
        task = res.scalar_one_or_none()

        if not task:
            logger.warning("[%s] execute_node: no task found at index %d, advancing", workflow_id, current_idx)
            return {"current_task_index": current_idx + 1}

        attempt = state.get("retry_count", 0) + 1
        idempotency_key = f"{workflow_id}_{task.id}_{attempt}"

        existing_res = await db.execute(
            select(ToolCallLog).where(ToolCallLog.idempotency_key == idempotency_key)
        )
        existing = existing_res.scalar_one_or_none()

        if existing and existing.status == ToolCallStatus.SUCCESS:
            logger.info("[%s] Task %d already succeeded (key=%s), skipping", workflow_id, current_idx, idempotency_key)
        else:
            task.status = ActionStatus.EXECUTING
            await db.commit()

            try:
                result_summary = await _dispatch_tool(task)
                status = ToolCallStatus.SUCCESS
                task.status = ActionStatus.EXECUTED
            except Exception as exc:
                result_summary = str(exc)
                status = ToolCallStatus.FAILED
                task.status = ActionStatus.FAILED
                logger.error("[%s] Task %d failed: %s", workflow_id, current_idx, exc)

            db.add(ToolCallLog(
                workflow_id=wf_uuid,
                action_id=task.id,
                tool_name=task.action_type.value,
                idempotency_key=idempotency_key,
                attempt_number=attempt,
                status=status,
                response_summary=result_summary[:2000],
            ))
            await db.commit()

    logger.info("[%s] execute_node: task %d/%d done", workflow_id, current_idx + 1, total)

    return {
        "current_task_index": current_idx + 1,
        "status": WorkflowStatus.EXECUTING.value,
    }


async def _dispatch_tool(task: Action) -> str:
    """Mock dispatcher for tools."""
    action_type = task.action_type
    draft = task.draft_payload.get("draft_text") if task.draft_payload else None
    
    msg = f"Executed {action_type.value}: '{task.description[:100]}'"
    if draft:
        # Include snippet of draft in log
        msg += f" | Draft: {draft[:200]}..."
    
    return msg
