"""
FastAPI Routes — Legal Due Diligence Agent (V2)
================================================
Endpoints:
  GET  /health               — health check (unauthenticated)
  POST /ingest               — async upload + ingest; returns job_id immediately
  GET  /status/{job_id}      — poll ingest progress
  GET  /conflicts            — current registry conflict report
  POST /plan                 — decompose goal → task list (document-aware)
  POST /execute              — run confirmed tasks → DueDiligenceReport
  POST /query  (legacy)      — single-shot query (unchanged)

Auth: all endpoints except /health and /status use Supabase JWT verification
      via get_supabase_claims from legal_rag's app/dependencies module.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

# --- Auth — direct import (same app as legal_rag) ---
from app.dependencies import get_supabase_claims

# --- Local modules ---
from due_diligence.db.postgres import init_schema
from due_diligence.db.pg_models import (
    create_session as db_create_session,
    complete_session as db_complete_session,
    log_escalation as db_log_escalation,
)
from due_diligence.escalation.triggers import EscalationManager
from due_diligence.ingestion.chunker import ClauseChunker
from due_diligence.ingestion.embedder import LegalEmbedder
from due_diligence.ingestion.parser import LegalDocumentParser
from due_diligence.ingestion.reference_parser import LLMReferenceParser
from due_diligence.ingestion.terms_extractor import DefinedTermExtractor
from due_diligence.intelligence.graph import DependencyGraph
from due_diligence.intelligence.registry import RegistryQueryEngine
from due_diligence.output.schemas import (
    Citation,
    ClauseReference,
    DefinitionalConflict,
    DocumentMeta,
    DueDiligenceReport,
    Escalation,
    EscalationAlert,
    Finding,
    FinalReport,
    TermDefinition,
)
from due_diligence.planner.decomposer import GoalDecomposer
from due_diligence.planner.executor import StateMachineExecutor
from due_diligence.retrieval.graph_expansion import GraphExpander
from due_diligence.retrieval.hybrid_search import HybridRetriever
from due_diligence.retrieval.reranker import LegalCrossEncoder
from due_diligence.synthesis.contradiction import ContradictionDetector
from due_diligence.synthesis.finding_gen import FindingGenerator

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

router = APIRouter()

# Initialise PostgreSQL schema on module load (safe to call multiple times)
try:
    init_schema()
except Exception as _e:
    print(f"⚠️  DB schema init failed: {_e}. Will retry on first request.")

# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_embedder: Optional[LegalEmbedder] = None
_retriever: Optional[HybridRetriever] = None
_reranker: Optional[LegalCrossEncoder] = None
_registry = RegistryQueryEngine()
_decomposer = GoalDecomposer()
_finding_gen = FindingGenerator()
_contradiction = ContradictionDetector()
_escalation_mgr = EscalationManager(confidence_threshold=0.5)
_graph = DependencyGraph()

# --- In-memory job store for async ingest ---
_jobs: Dict[str, Dict] = {}
_jobs_lock = asyncio.Lock()

# --- In-memory document manifest (populated by ingest, used by planner) ---
_document_manifest: List[Dict] = []


def _get_embedder() -> LegalEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = LegalEmbedder()
    return _embedder


def _get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        e = _get_embedder()
        _retriever = HybridRetriever(
            client=e.qdrant_client,
            registry=_registry,  # enables defined-terms conflict annotation
        )
    return _retriever


def _get_reranker() -> LegalCrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = LegalCrossEncoder()
    return _reranker


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class PlanRequest(BaseModel):
    goal: str


class PlanResponse(BaseModel):
    goal: str
    tasks: List[dict]


class ExecuteRequest(BaseModel):
    goal: str
    tasks: List[dict]
    document_names: List[str] = []


class QueryRequest(BaseModel):
    query: str
    document_ids: List[str] = []


# ---------------------------------------------------------------------------
# Background ingest pipeline
# ---------------------------------------------------------------------------

async def _run_ingest(job_id: str, tmp_path: str, doc_name: str, suffix: str):
    """
    Full ingestion pipeline run as a background task.
    Updates _jobs[job_id] at each stage.
    """
    global _document_manifest

    async def _set(status: str, progress: int, **kwargs):
        async with _jobs_lock:
            _jobs[job_id].update({"status": status, "progress": progress, **kwargs})

    try:
        await _set("running", 5)
        parser = LegalDocumentParser()

        await _set("parsing", 15)
        if suffix == ".pdf":
            raw_blocks = parser.parse_pdf(tmp_path)
        else:
            raw_blocks = parser.parse_docx(tmp_path)

        await _set("chunking", 30)
        chunker = ClauseChunker(body_font_size=parser.body_font_size)
        chunks = chunker.build_chunks(raw_blocks)

        await _set("extracting_terms", 45)
        extractor = DefinedTermExtractor()
        extractor.extract_and_store(chunks, document_name=doc_name)

        await _set("parsing_references", 60)
        ref_parser = LLMReferenceParser()
        enriched_chunks = ref_parser.resolve_references(chunks, doc_name=doc_name, graph=_graph)

        await _set("building_graph", 70)
        _graph.build_graph(enriched_chunks, doc_name=doc_name)

        await _set("embedding", 80)
        embedder = _get_embedder()
        embedder.index_document(doc_name, enriched_chunks)

        # Conflict check
        await _set("finalising", 92)
        conflicts_raw = _registry.detect_conflicts()
        pre_conflicts = [
            DefinitionalConflict(
                term=term,
                definitions=[
                    TermDefinition(
                        term=term,
                        definition=d["definition"],
                        document_name=d["document"],
                        hierarchy_path=d["path"],
                    )
                    for d in defs
                ],
            )
            for term, defs in conflicts_raw.items()
        ]

        page_count = max((b.get("page_number", 1) for b in raw_blocks), default=1)

        doc_meta = DocumentMeta(
            document_name=doc_name,
            file_type=suffix.lstrip("."),
            chunk_count=len(chunks),
            page_count=page_count,
        )

        # Update manifest
        _document_manifest = [m for m in _document_manifest if m["name"] != doc_name]
        _document_manifest.append({
            "name": doc_name,
            "chunk_count": len(chunks),
            "page_count": page_count,
        })

        result = {
            "document": doc_meta.model_dump(),
            "pre_ingestion_conflicts": [c.model_dump() for c in pre_conflicts],
            "graph_nodes": _graph.number_of_nodes(),
            "graph_edges": _graph.number_of_edges(),
        }
        await _set("complete", 100, result=result, error=None)
        print(f"✅ Ingest complete for '{doc_name}' (job {job_id})")

    except Exception as e:
        await _set("failed", 0, error=str(e), result=None)
        print(f"❌ Ingest failed for '{doc_name}' (job {job_id}): {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Health (unauthenticated)
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ---------------------------------------------------------------------------
# POST /ingest — async upload (returns job_id immediately)
# ---------------------------------------------------------------------------

@router.post("/ingest")
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _claims: dict = Depends(get_supabase_claims),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".pdf", ".docx"):
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are supported.")

    # Save to temp file synchronously (small enough to be fine on the event loop)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    job_id = str(uuid.uuid4())
    async with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "progress": 0, "result": None, "error": None}

    background_tasks.add_task(_run_ingest, job_id, tmp_path, file.filename, suffix)
    return {"job_id": job_id, "status": "queued", "document_name": file.filename}


# ---------------------------------------------------------------------------
# GET /status/{job_id} — poll ingest progress (unauthenticated, ID is opaque)
# ---------------------------------------------------------------------------

@router.get("/status/{job_id}")
async def get_ingest_status(job_id: str):
    async with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"job_id": job_id, **job}


# ---------------------------------------------------------------------------
# GET /conflicts — current registry conflict report
# ---------------------------------------------------------------------------

@router.get("/conflicts")
async def get_conflicts(_claims: dict = Depends(get_supabase_claims)):
    try:
        conflicts_raw = _registry.detect_conflicts()
        conflicts = [
            DefinitionalConflict(
                term=term,
                definitions=[
                    TermDefinition(
                        term=term,
                        definition=d["definition"],
                        document_name=d["document"],
                        hierarchy_path=d["path"],
                    )
                    for d in defs
                ],
            )
            for term, defs in conflicts_raw.items()
        ]
        return {"conflict_count": len(conflicts), "conflicts": [c.model_dump() for c in conflicts]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /plan — document-aware goal decomposition
# ---------------------------------------------------------------------------

@router.post("/plan", response_model=PlanResponse)
async def plan_goal(req: PlanRequest, _claims: dict = Depends(get_supabase_claims)):
    try:
        conflicts_raw = _registry.detect_conflicts()
        pre_conflicts = [
            {"term": term, "definitions": defs}
            for term, defs in conflicts_raw.items()
        ]
        tasks = _decomposer.decompose_goal(
            goal=req.goal,
            document_manifest=_document_manifest,
            pre_conflicts=pre_conflicts,
        )
        if not tasks:
            raise HTTPException(
                status_code=422,
                detail="Planner failed to decompose the goal. Try rephrasing.",
            )
        return PlanResponse(goal=req.goal, tasks=tasks)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /execute — run confirmed task list → DueDiligenceReport
# ---------------------------------------------------------------------------

@router.post("/execute", response_model=DueDiligenceReport)
async def execute_plan(
    req: ExecuteRequest,
    _claims: dict = Depends(get_supabase_claims),
):
    session_id = str(uuid.uuid4())

    # Create session record in Postgres
    try:
        db_create_session(session_id, req.goal, req.document_names)
    except Exception as e:
        print(f"⚠️  Session create failed: {e}")

    try:
        embedder = _get_embedder()
        retriever = _get_retriever()
        expander = GraphExpander(_graph)

        executor = StateMachineExecutor(
            graph=_graph,
            registry=_registry,
            retriever=retriever,
            embedder=embedder,
        )
        log = executor.execute_plan(req.tasks)

        findings: List[Finding] = []
        all_escalations: List[Escalation] = []

        for record in log:
            if record.result is None:
                continue

            raw_clauses = []
            if isinstance(record.result, list):
                raw_clauses = record.result
            elif isinstance(record.result, dict):
                raw_clauses = record.result.get("definitions", [])

            # Rerank if clauses carry scores
            if raw_clauses and all("score" in c or "rerank_score" in c for c in raw_clauses):
                ranked = _get_reranker().rerank(record.search_target, raw_clauses, top_k=5)
            else:
                ranked = raw_clauses

            # Graph expansion
            expanded = []
            if ranked:
                expanded = expander.expand_context(ranked)

            finding = _finding_gen.generate_finding(
                task_id=str(record.task_id),
                task_description=record.reason or record.search_target,
                active_clauses=ranked,
                reference_chain=expanded,
                registry=_registry,
            )
            findings.append(finding)

            # Run all three escalation triggers
            escalations = _escalation_mgr.evaluate_finding(finding)
            all_escalations.extend(escalations)

            # Log each escalation to Postgres audit_log
            for esc in escalations:
                try:
                    db_log_escalation(
                        session_id=session_id,
                        task_id=str(record.task_id),
                        trigger_type=esc.trigger,
                        reviewer_note=esc.reviewer_note,
                        context=esc.context,
                    )
                except Exception as e:
                    print(f"  ⚠️ Audit log write failed: {e}")

        # Cross-task contradiction detection
        contradictions = _contradiction.analyze_findings(findings)

        # Pre-ingestion conflicts
        conflicts_raw = _registry.detect_conflicts()
        pre_conflicts = [
            DefinitionalConflict(
                term=term,
                definitions=[
                    TermDefinition(
                        term=term,
                        definition=d["definition"],
                        document_name=d["document"],
                        hierarchy_path=d["path"],
                    )
                    for d in defs
                ],
            )
            for term, defs in conflicts_raw.items()
        ]

        # Mark session complete
        try:
            db_complete_session(session_id)
        except Exception as e:
            print(f"⚠️  Session complete failed: {e}")

        docs_analysed = [
            DocumentMeta(
                document_name=d["name"],
                file_type="pdf",
                chunk_count=d.get("chunk_count", 0),
                page_count=d.get("page_count", 0),
            )
            for d in _document_manifest
        ]

        return DueDiligenceReport(
            goal=req.goal,
            session_id=session_id,
            documents_analysed=docs_analysed,
            generated_at=datetime.utcnow(),
            pre_ingestion_conflicts=pre_conflicts,
            findings=findings,
            cross_document_contradictions=contradictions,
            escalations=all_escalations,
            embedding_model_used="voyage-law-2 (primary) + nomic-embed-text-v1.5 (backup) + SPLADE | RRF",
        )

    except Exception as e:
        # Best-effort session failure mark
        try:
            db_complete_session(session_id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# POST /query — legacy single-shot endpoint (unchanged, backward compat)
# ---------------------------------------------------------------------------

@router.post("/query", response_model=FinalReport)
async def process_legal_query(
    req: QueryRequest,
    _claims: dict = Depends(get_supabase_claims),
):
    try:
        embedder = _get_embedder()
        retriever = _get_retriever()

        bge_vec = embedder.get_bge_query_vector(req.query)
        lb_vec = embedder.get_legal_bert_query_vector(req.query)
        splade_vec = embedder.get_splade_query_vector(req.query)

        vector_hits = retriever.search(req.query, bge_vec, lb_vec, splade_vec, limit=5)
        ranked_nodes = _get_reranker().rerank(req.query, vector_hits, top_k=3)

        escalations = _escalation_mgr.check_for_escalations(ranked_nodes)
        contradiction = _contradiction.analyze(ranked_nodes)
        if contradiction:
            escalations.append(contradiction)

        active_clauses = [
            ClauseReference(
                node_id=n.get("node_id", ""),
                source_document=n.get("source_document", "Unknown"),
                exact_text=n.get("text", ""),
                rerank_score=n.get("rerank_score", 0.0),
            )
            for n in ranked_nodes
        ]

        fg = FindingGenerator()
        report_text = fg._build_context_block(ranked_nodes, None)

        return FinalReport(
            query=req.query,
            answer=report_text,
            active_clauses=active_clauses,
            escalations=escalations if escalations else None,
            is_safe_to_execute=len(escalations) == 0,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
