"""
Full Pydantic V2 Output Schema
===============================
7 models matching the V1 spec exactly.

Backward-compatible shims (EscalationAlert, ClauseReference, FinalReport)
are kept at the bottom so the existing /query route in routes.py continues
to work unchanged.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core building blocks
# ---------------------------------------------------------------------------

class TermDefinition(BaseModel):
    """One document's definition of a term."""
    term: str
    definition: str
    document_name: str
    hierarchy_path: str  # e.g. "ARTICLE I > Section 1.1"


class DefinitionalConflict(BaseModel):
    """Same term defined differently across two or more documents."""
    term: str
    definitions: List[TermDefinition]  # one per document that defines it


class Citation(BaseModel):
    """Clause-level provenance for any claim or reference."""
    document_name: str
    page_number: int
    clause_reference: str   # e.g. "Section 4.2(b)"
    clause_text: str
    relevance_score: float = 0.0


# ---------------------------------------------------------------------------
# Per-task output
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """The synthesised result of a single planner task."""
    task_id: str
    task_description: str
    claim: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_citations: List[Citation]
    reference_chain: Optional[List[Citation]] = None   # graph-expanded dependencies
    definitional_conflicts: Optional[List[DefinitionalConflict]] = None
    escalated: bool = False
    escalation_type: Optional[str] = None  # insufficient_coverage | definitional_conflict | structural_ambiguity


# ---------------------------------------------------------------------------
# Cross-task / cross-document analysis
# ---------------------------------------------------------------------------

class Contradiction(BaseModel):
    """Two findings that make conflicting claims about the same obligation."""
    claim_a: str
    source_a: Citation
    claim_b: str
    source_b: Citation
    description: str  # plain-English explanation of the conflict


# ---------------------------------------------------------------------------
# Escalation (typed + reviewer-actionable)
# ---------------------------------------------------------------------------

class Escalation(BaseModel):
    """
    One of three typed escalations.
    trigger ∈ {insufficient_coverage, definitional_conflict, structural_ambiguity}
    """
    task_id: str
    trigger: str
    context: str                      # what the agent was trying to decide
    retrieved_chunks: List[Citation]  # what was actually retrieved
    reviewer_note: str                # what specifically a human needs to assess


# ---------------------------------------------------------------------------
# Top-level report
# ---------------------------------------------------------------------------

class DocumentMeta(BaseModel):
    document_name: str
    file_type: str          # pdf | docx
    chunk_count: int
    page_count: int


class DueDiligenceReport(BaseModel):
    goal: str
    session_id: Optional[str] = None          # Postgres deal_sessions.session_id
    documents_analysed: List[DocumentMeta]
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    pre_ingestion_conflicts: List[DefinitionalConflict] = []
    findings: List[Finding] = []
    cross_document_contradictions: List[Contradiction] = []
    escalations: List[Escalation] = []
    embedding_model_used: str = "voyage-law-2 (primary) + nomic-embed-text-v1.5 (backup) + SPLADE | RRF"


# ---------------------------------------------------------------------------
# Backward-compatible shims for existing /query route
# ---------------------------------------------------------------------------

class ClauseReference(BaseModel):
    node_id: str
    source_document: str
    exact_text: str
    rerank_score: float


class EscalationAlert(BaseModel):
    trigger_type: str = Field(..., description="LOW_CONFIDENCE | UNKNOWN_EDGE | CONTRADICTION")
    reason: str
    flagged_nodes: List[str]


class FinalReport(BaseModel):
    query: str
    answer: str
    active_clauses: List[ClauseReference]
    escalations: Optional[List[EscalationAlert]] = None
    is_safe_to_execute: bool = True
