"""
Goal Decomposer — Document-Aware Planner
=========================================
Translates a high-level due diligence goal into a concrete, prioritised
task list. Now receives:
  - document_manifest: list of {name, chunk_count, page_count} for all
    ingested documents in the current deal folder
  - pre_conflicts:     list of {term, definitions} from the registry

The planner is framed for legal due diligence (not the old "Enterprise
Partner Intelligence Agent" framing), and the task JSON schema is extended:
  task_id, tool, search_target, reason, priority (1-5), relevant_documents
"""

import json
import os
from typing import Dict, List

from openai import OpenAI


class GoalDecomposer:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get(
                "OPENAI_API_BASE", "https://api.groq.com/openai/v1"
            ),
        )
        self.model = "llama-3.3-70b-versatile"

    def decompose_goal(
        self,
        goal: str,
        document_manifest: List[Dict] = None,
        pre_conflicts: List[Dict] = None,
    ) -> List[Dict]:
        """
        Decomposes a due diligence goal into an executable task list.

        Args:
            goal:              User's natural-language due diligence goal.
            document_manifest: [{name, chunk_count, page_count}] for each indexed doc.
            pre_conflicts:     [{term, definitions}] from registry.detect_conflicts().
        """
        print(f"[Planner] Decomposing goal: '{goal[:80]}'...")

        # --- Build manifest section ---
        manifest_text = "No documents indexed yet."
        if document_manifest:
            lines = [
                f"  - {d['name']} ({d.get('chunk_count', '?')} chunks, "
                f"{d.get('page_count', '?')} pages)"
                for d in document_manifest
            ]
            manifest_text = "Documents in this deal folder:\n" + "\n".join(lines)

        # --- Build conflict section ---
        conflict_text = "No pre-ingestion definitional conflicts detected."
        if pre_conflicts:
            lines = []
            for c in pre_conflicts[:10]:  # cap at 10 for prompt size
                term = c.get("term", "")
                docs = [
                    d.get("document_name", d.get("document", "?"))
                    for d in c.get("definitions", [])
                ]
                lines.append(f"  - '{term}' defined differently in: {', '.join(docs)}")
            conflict_text = "Pre-ingestion definitional conflicts:\n" + "\n".join(lines)

        # --- Available tools ---
        tools_desc = """
Available tools:
  1. "hybrid_search"   — Semantic + keyword retrieval from Qdrant. Use for clause-level facts.
  2. "registry_check"  — Check PostgreSQL registry for defined terms and cross-document conflicts.
  3. "graph_search"    — Traverse FalkorDB cross-reference graph for dependency chains.
"""

        prompt = f"""You are a senior legal due diligence analyst orchestrating an AI agent.
Your job is to decompose a legal due diligence goal into a prioritised sequence of
specific, executable retrieval and analysis tasks.

{manifest_text}

{conflict_text}

{tools_desc}

Due Diligence Goal: {goal}

Instructions:
- Generate 4–8 tasks covering the full scope of the goal.
- Assign priority 1 (highest) to 5 (lowest) based on risk relevance.
- For each task, list only the documents most likely to contain the relevant clauses.
- Use registry_check whenever a defined term conflict is relevant to the task.
- Use graph_search when a clause is likely to depend on another clause referenced elsewhere.

You MUST output valid JSON in this exact structure:
{{
  "tasks": [
    {{
      "task_id": 1,
      "tool": "tool_name",
      "search_target": "Specific clause, term, or obligation to investigate",
      "reason": "Why this task is necessary for the goal",
      "priority": 1,
      "relevant_documents": ["doc_name.pdf"]
    }}
  ]
}}
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            parsed = json.loads(raw)
            tasks = parsed.get("tasks", [])
            print(f"  ↳ Generated {len(tasks)} tasks.")
            return tasks

        except Exception as e:
            print(f"⚠️  Goal decomposition failed: {e}")
            return []
