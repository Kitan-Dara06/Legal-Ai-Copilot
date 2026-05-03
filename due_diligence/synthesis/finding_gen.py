"""
Structured Finding Generator
=============================
Replaces the free-form Markdown ReportGenerator with a structured
FindingGenerator that emits Pydantic Finding objects.

Changes from the old ReportGenerator:
  - Prompts the LLM to return JSON matching the Finding schema.
  - Populates reference_chain from graph-expanded clauses.
  - Populates definitional_conflicts by querying the registry for terms
    present in the retrieved chunks.
  - Confidence = mean rerank_score of supporting_citations.
  - escalated = True if confidence < 0.5.
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from openai import OpenAI

# Import from due_diligence.output.schemas — run from backend/ directory
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from due_diligence.output.schemas import (
    Citation,
    DefinitionalConflict,
    Finding,
    TermDefinition,
)


class FindingGenerator:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_API_BASE", "https://api.groq.com/openai/v1"),
        )
        self.model = "llama-3.3-70b-versatile"

    def generate_finding(
        self,
        task_id: str,
        task_description: str,
        active_clauses: List[Dict],
        reference_chain: Optional[List[Dict]] = None,
        registry=None,
    ) -> Finding:
        """
        Generates a structured Finding from retrieved + graph-expanded clauses.

        Args:
            task_id:           e.g. "task_1"
            task_description:  the planner task description
            active_clauses:    reranked hits from hybrid search
            reference_chain:   graph-expanded dependencies (optional)
            registry:          RegistryQueryEngine for conflict lookups (optional)
        """
        if not active_clauses:
            return Finding(
                task_id=task_id,
                task_description=task_description,
                claim="No relevant clauses were retrieved for this task.",
                confidence=0.0,
                supporting_citations=[],
                escalated=True,
                escalation_type="insufficient_coverage",
            )

        # --- Build citation objects ---
        citations = [
            Citation(
                document_name=c.get("source_document", c.get("document_name", "Unknown")),
                page_number=int(c.get("page_number", 0)),
                clause_reference=c.get("clause_reference", c.get("node_id", "")),
                clause_text=c.get("text", ""),
                relevance_score=round(float(c.get("rerank_score", c.get("score", 0.0))), 4),
            )
            for c in active_clauses
        ]

        # --- Reference chain citations ---
        ref_citations: Optional[List[Citation]] = None
        if reference_chain:
            ref_citations = [
                Citation(
                    document_name=r.get("source_document", "Unknown"),
                    page_number=int(r.get("page_number", 0)),
                    clause_reference=r.get("node", r.get("node_id", "")),
                    clause_text=r.get("text", ""),
                    relevance_score=0.0,
                )
                for r in reference_chain
                if r.get("text")
            ]

        # --- Definitional conflict lookup ---
        def_conflicts: Optional[List[DefinitionalConflict]] = None
        if registry:
            all_text = " ".join(c.get("text", "") for c in active_clauses)
            conflicts = registry.detect_conflicts()
            matched = []
            for term, defs in conflicts.items():
                if re.search(r"\b" + re.escape(term) + r"\b", all_text, re.IGNORECASE):
                    matched.append(
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
                    )
            if matched:
                def_conflicts = matched

        # --- Confidence score ---
        scores = [c.relevance_score for c in citations if c.relevance_score > 0]
        confidence = round(sum(scores) / len(scores), 4) if scores else 0.0

        # --- LLM synthesis → structured JSON ---
        context_block = self._build_context_block(active_clauses, reference_chain)
        conflict_note = ""
        if def_conflicts:
            conflict_note = "\n\nNOTE — Definitional Conflicts Detected:\n"
            for dc in def_conflicts:
                conflict_note += f"  Term '{dc.term}' is defined differently across documents:\n"
                for td in dc.definitions:
                    conflict_note += f"    [{td.document_name}]: {td.definition[:120]}\n"

        prompt = f"""You are a legal due diligence analyst.
Answer the following task based STRICTLY on the provided clauses.
Do NOT use outside knowledge. If the clauses are insufficient, say so.

Task: {task_description}

Clauses:
{context_block}{conflict_note}

Return ONLY valid JSON in this exact structure:
{{
  "claim": "Your synthesised finding in 1-3 sentences.",
  "confidence_note": "Brief rationale for your certainty level."
}}
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            parsed = json.loads(raw)
            claim = parsed.get("claim", "Finding generation failed.")
        except Exception as e:
            claim = f"Synthesis error: {e}"

        escalated = confidence < 0.5
        escalation_type = None
        if escalated:
            escalation_type = "definitional_conflict" if def_conflicts else "insufficient_coverage"

        return Finding(
            task_id=task_id,
            task_description=task_description,
            claim=claim,
            confidence=confidence,
            supporting_citations=citations,
            reference_chain=ref_citations if ref_citations else None,
            definitional_conflicts=def_conflicts,
            escalated=escalated,
            escalation_type=escalation_type,
        )

    def _build_context_block(
        self, clauses: List[Dict], chain: Optional[List[Dict]]
    ) -> str:
        parts = []
        for i, c in enumerate(clauses, 1):
            parts.append(
                f"[Clause {i}]\n"
                f"Source: {c.get('source_document', c.get('document_name', 'Unknown'))}\n"
                f"Location: {c.get('clause_reference', c.get('node_id', ''))}\n"
                f"Text: {c.get('text', '')}\n"
            )
        if chain:
            parts.append("\n[Graph-Expanded Dependencies]")
            for item in chain:
                parts.append(f"  [{item.get('node', '')}]: {item.get('text', '')[:300]}")
        return "\n".join(parts)
