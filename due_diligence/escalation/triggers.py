"""
Three Typed Escalation Triggers
=================================
Replaces the generic EscalationManager with three distinct, spec-compliant
trigger methods, each producing an Escalation object with a reviewer_note.

Trigger types:
  insufficient_coverage    — < 3 relevant chunks retrieved
  definitional_conflict    — a key term in the finding is defined differently
                             across documents
  structural_ambiguity     — LLM classifies the clause language as intrinsically
                             underspecified (ambiguous regardless of retrieval quality)

Each reviewer_note is human-actionable: it tells the reviewer exactly what
to check, not just that a problem exists.
"""

import json
import os
from typing import Dict, List, Optional

from openai import OpenAI

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from due_diligence.output.schemas import (
    Citation,
    DefinitionalConflict,
    Escalation,
    EscalationAlert,  # kept for backward compat with /query route
    Finding,
)

INSUFFICIENT_COVERAGE_THRESHOLD = 3  # minimum chunks required


class EscalationManager:
    """
    Three typed escalation triggers.
    Each returns an Escalation object with a populated reviewer_note.

    Also exposes check_for_escalations() as a shim for the existing /query route.
    """

    def __init__(self, confidence_threshold: float = 0.5):
        self.confidence_threshold = confidence_threshold
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_API_BASE", "https://api.groq.com/openai/v1"),
        )
        self.model = "llama-3.3-70b-versatile"

    # ------------------------------------------------------------------
    # Trigger 1: Insufficient Coverage
    # ------------------------------------------------------------------

    def check_insufficient_coverage(
        self, finding: Finding
    ) -> Optional[Escalation]:
        """
        Fires when fewer than 3 supporting citations were retrieved.
        reviewer_note tells the human what clause types to search manually.
        """
        n = len(finding.supporting_citations)
        if n >= INSUFFICIENT_COVERAGE_THRESHOLD:
            return None

        retrieved_summary = (
            f"{n} chunk(s) retrieved"
            if n > 0
            else "No chunks retrieved"
        )

        reviewer_note = (
            f"Only {n} clause(s) retrieved for task '{finding.task_description}'. "
            f"The document set may not explicitly address this obligation, or the retrieval "
            f"model may have missed relevant clauses. Manually check: definitions sections, "
            f"any schedules or exhibits referenced in the main body, and any side letters "
            f"or amendments that may address this topic directly."
        )

        return Escalation(
            task_id=finding.task_id,
            trigger="insufficient_coverage",
            context=f"Task: {finding.task_description}. {retrieved_summary}.",
            retrieved_chunks=finding.supporting_citations,
            reviewer_note=reviewer_note,
        )

    # ------------------------------------------------------------------
    # Trigger 2: Definitional Conflict
    # ------------------------------------------------------------------

    def check_definitional_conflict(
        self, finding: Finding
    ) -> Optional[Escalation]:
        """
        Fires when the finding already has definitional_conflicts populated.
        reviewer_note lists the conflicting documents and their definitions
        so the reviewer can determine which definition governs.
        """
        if not finding.definitional_conflicts:
            return None

        conflict_lines = []
        for dc in finding.definitional_conflicts:
            conflict_lines.append(f"\nTerm: '{dc.term}'")
            for td in dc.definitions:
                conflict_lines.append(
                    f"  [{td.document_name} | {td.hierarchy_path}]: "
                    f"{td.definition[:200]}"
                )

        reviewer_note = (
            f"The finding for task '{finding.task_description}' depends on "
            f"{len(finding.definitional_conflicts)} term(s) that are defined "
            f"differently across documents. The interpretation of this finding "
            f"changes depending on which definition governs. "
            f"Review the governing law clause and precedence order to resolve:\n"
            + "\n".join(conflict_lines)
        )

        return Escalation(
            task_id=finding.task_id,
            trigger="definitional_conflict",
            context=(
                f"Task: {finding.task_description}. "
                f"{len(finding.definitional_conflicts)} conflicting term(s) detected in retrieved clauses."
            ),
            retrieved_chunks=finding.supporting_citations,
            reviewer_note=reviewer_note,
        )

    # ------------------------------------------------------------------
    # Trigger 3: Structural Ambiguity (LLM-classified)
    # ------------------------------------------------------------------

    def check_structural_ambiguity(
        self, finding: Finding
    ) -> Optional[Escalation]:
        """
        Fires when an LLM classifier determines that the underlying clause
        language is intrinsically underspecified — ambiguous regardless of
        how much additional context is retrieved.

        Uses a dedicated prompt separate from the synthesis LLM call.
        """
        if not finding.supporting_citations:
            return None

        # Use the highest-scoring citation as the primary clause for inspection
        primary = max(
            finding.supporting_citations, key=lambda c: c.relevance_score
        )
        clause_text = primary.clause_text

        prompt = f"""You are a legal precision analyst.
Read the following clause and determine ONLY whether the language itself is
intrinsically underspecified — i.e., ambiguous in a way that no amount of
additional document context would resolve. Do NOT consider missing facts
or absent clauses; only evaluate the text as written.

Examples of structural ambiguity:
  - "reasonable efforts" with no definition of what constitutes reasonable
  - "material breach" without a materiality threshold
  - "as soon as practicable" without a backstop date
  - Obligations that are conditional on unstated criteria

Clause text:
"{clause_text}"

Respond ONLY with valid JSON:
{{
  "is_structurally_ambiguous": true | false,
  "explanation": "Plain-English reason why the language is ambiguous (or 'Not ambiguous')."
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)

            if result.get("is_structurally_ambiguous"):
                explanation = result.get("explanation", "Clause is underspecified.")
                reviewer_note = (
                    f"The primary clause for task '{finding.task_description}' contains "
                    f"language that is intrinsically ambiguous. No additional retrieval "
                    f"will resolve this — it requires legal judgment or renegotiation.\n\n"
                    f"Clause: [{primary.clause_reference}] in {primary.document_name}\n\n"
                    f"Why it is ambiguous: {explanation}"
                )
                return Escalation(
                    task_id=finding.task_id,
                    trigger="structural_ambiguity",
                    context=(
                        f"Task: {finding.task_description}. "
                        f"Clause [{primary.clause_reference}] classified as structurally ambiguous."
                    ),
                    retrieved_chunks=finding.supporting_citations,
                    reviewer_note=reviewer_note,
                )

        except Exception as e:
            print(f"  ⚠️ Structural ambiguity check failed for task {finding.task_id}: {e}")

        return None

    # ------------------------------------------------------------------
    # Convenience: run all three triggers on a finding
    # ------------------------------------------------------------------

    def evaluate_finding(self, finding: Finding) -> List[Escalation]:
        """Runs all three triggers and returns any that fire."""
        escalations = []

        e1 = self.check_insufficient_coverage(finding)
        if e1:
            escalations.append(e1)
            print(f"  ⚠️ ESCALATED [{e1.trigger}] — task {finding.task_id}")

        e2 = self.check_definitional_conflict(finding)
        if e2:
            escalations.append(e2)
            print(f"  ⚠️ ESCALATED [{e2.trigger}] — task {finding.task_id}")

        e3 = self.check_structural_ambiguity(finding)
        if e3:
            escalations.append(e3)
            print(f"  ⚠️ ESCALATED [{e3.trigger}] — task {finding.task_id}")

        return escalations

    # ------------------------------------------------------------------
    # Backward-compatible shim for existing /query route (raw node dicts)
    # ------------------------------------------------------------------

    def check_for_escalations(self, ranked_nodes: List[Dict]) -> List[EscalationAlert]:
        """Legacy method — keeps the /query route working unchanged."""
        alerts = []
        for node in ranked_nodes:
            if node.get("rerank_score", 0) < self.confidence_threshold:
                alerts.append(
                    EscalationAlert(
                        trigger_type="LOW_CONFIDENCE",
                        reason=(
                            f"Cross-Encoder score ({node.get('rerank_score', 0)}) "
                            f"below threshold ({self.confidence_threshold})."
                        ),
                        flagged_nodes=[node.get("node_id", "")],
                    )
                )
            if node.get("edge_type") == "UNKNOWN":
                alerts.append(
                    EscalationAlert(
                        trigger_type="UNKNOWN_EDGE",
                        reason="LLM failed to classify the legal relationship of this cross-reference.",
                        flagged_nodes=[node.get("node_id", "")],
                    )
                )
        return alerts
