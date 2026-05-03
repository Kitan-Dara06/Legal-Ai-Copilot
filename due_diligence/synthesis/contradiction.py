"""
Cross-Task + Cross-Document Contradiction Detector
====================================================
Previously ran on raw clauses within a single query.
Now operates on the full list of Finding objects after all planner
tasks complete — detecting when two findings make conflicting claims
about the same obligation.

Returns list[Contradiction] (typed schema objects).
"""

import json
import os
import re
from typing import List, Optional

from openai import OpenAI

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from due_diligence.output.schemas import Citation, Contradiction, EscalationAlert, Finding


class ContradictionDetector:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_API_BASE", "https://api.groq.com/openai/v1"),
        )
        self.model = "llama-3.3-70b-versatile"

    # ------------------------------------------------------------------
    # New: cross-task analysis (Findings → Contradictions)
    # ------------------------------------------------------------------

    def analyze_findings(self, findings: List[Finding]) -> List[Contradiction]:
        """
        Compare all Finding pairs for conflicting claims.
        Returns a list of typed Contradiction objects.

        Only compares pairs where both findings have at least one citation
        so that Contradictions always have source provenance.
        """
        if len(findings) < 2:
            return []

        contradictions: List[Contradiction] = []

        # Compare every ordered pair (a, b) where a < b to avoid duplicates
        for i in range(len(findings)):
            for j in range(i + 1, len(findings)):
                finding_a = findings[i]
                finding_b = findings[j]

                # Skip pairs with no citations — cannot attribute the contradiction
                if not finding_a.supporting_citations or not finding_b.supporting_citations:
                    continue

                contradiction = self._compare_pair(finding_a, finding_b)
                if contradiction:
                    contradictions.append(contradiction)

        return contradictions

    def _compare_pair(
        self, finding_a: Finding, finding_b: Finding
    ) -> Optional[Contradiction]:
        """LLM comparison of two findings — returns Contradiction or None."""
        prompt = f"""You are a legal auditor reviewing a due diligence report.
Compare the following two findings and determine if they make logically contradictory
claims about the same obligation, right, or liability.

Finding A (Task {finding_a.task_id}):
"{finding_a.claim}"

Finding B (Task {finding_b.task_id}):
"{finding_b.claim}"

Are these contradictory? Respond ONLY with valid JSON:
{{
  "contradiction_found": true | false,
  "description": "Explanation of the conflict (or 'No contradiction' if false)."
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

            if result.get("contradiction_found"):
                # Use the first citation of each finding as the source
                source_a = finding_a.supporting_citations[0]
                source_b = finding_b.supporting_citations[0]

                return Contradiction(
                    claim_a=finding_a.claim,
                    source_a=source_a,
                    claim_b=finding_b.claim,
                    source_b=source_b,
                    description=result.get("description", "Contradiction detected."),
                )

        except Exception as e:
            print(f"  ⚠️ Contradiction check failed for tasks "
                  f"{finding_a.task_id} vs {finding_b.task_id}: {e}")

        return None

    # ------------------------------------------------------------------
    # Backward-compatible shim — raw clauses within a single query
    # (used by the existing /query route in routes.py)
    # ------------------------------------------------------------------

    def analyze(self, clauses: List[dict]) -> Optional[EscalationAlert]:
        """Legacy method: takes raw clause dicts, returns EscalationAlert or None."""
        if len(clauses) < 2:
            return None

        context = "\n".join([f"[{c.get('node_id', '')}]: {c.get('text', '')}" for c in clauses])

        prompt = f"""You are a legal auditor. Review these active clauses.
Do they logically contradict each other in a way that creates legal ambiguity?

Clauses:
{context}

Respond ONLY with a JSON object:
{{"contradiction_found": true/false, "reason": "brief explanation"}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)

            if result.get("contradiction_found"):
                return EscalationAlert(
                    trigger_type="CONTRADICTION",
                    reason=result.get("reason", ""),
                    flagged_nodes=[c.get("node_id", "") for c in clauses],
                )
        except Exception as e:
            print(f"Contradiction check failed: {e}")

        return None
