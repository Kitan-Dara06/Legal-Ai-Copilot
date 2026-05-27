"""
Decision Brief — Standalone Test Harness
=========================================
Tests `generate_decision_brief()` end-to-end without LangGraph or any DB writes.

Pipeline:
  1. Embed the goal with voyage-law-2
  2. Hybrid search (Qdrant RRF + Voyage reranker) → top-k chunks
  3. Pass chunks + goal to Groq (llama-3.3-70b) → DecisionBriefResult
  4. Pretty-print the result so you can evaluate it as a lawyer would

Run with:
  ./venv/bin/python3 scripts/test_decision_brief.py \\
      --org-id  <your-org-uuid> \\
      --workspace-id <your-workspace-uuid> \\
      --goal "Draft a response to the termination notice in clause 15"

Optional flags:
  --filename  "contract.pdf"   # scope search to one document
  --top-k     8                # number of chunks to retrieve (default: 8)
  --out       brief.json       # also write result to JSON file
"""

import argparse
import asyncio
import json
import os
import sys
import textwrap
from datetime import date
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

# ── Pydantic schemas ────────────────────────────────────────────────────────────
from pydantic import BaseModel, Field


class ActionRecommendation(BaseModel):
    action_type: str  # DRAFT_RESPONSE | DRAFT_NOTICE | DRAFT_AMENDMENT
    description: str
    urgency: float = Field(ge=0.0, le=1.0)
    deadline_date: Optional[date] = None
    deadline_source: Optional[str] = None


class RelevantClause(BaseModel):
    clause_ref: str
    excerpt: str
    relevance_reason: str
    document_id: str  # UUID as str — no DB lookup needed here


class ConflictFlag(BaseModel):
    type: str  # DEFINITIONAL_CONFLICT | DEADLINE_CONFLICT | STRUCTURAL_AMBIGUITY
    description: str
    source_a: str
    source_b: Optional[str] = None


class DecisionBriefResult(BaseModel):
    goal: str
    summary: str
    recommended_actions: list[ActionRecommendation]
    relevant_clauses: list[RelevantClause]
    conflicts_to_resolve: list[ConflictFlag]
    deadlines_implicated: list[dict]
    verification_checklist: list[str]
    proceed_recommended: bool
    proceed_reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)


# ── Core logic ──────────────────────────────────────────────────────────────────

def _retrieve(
    goal: str,
    org_id: str,
    workspace_id: str,
    filename: Optional[str],
    top_k: int,
) -> list[dict]:
    """Run hybrid retrieval using the live Qdrant + Voyage stack."""
    from app.services.ingestion.embedder import LegalEmbedder
    from app.services.store import search_hybrid

    print(f"\n[1/3] Embedding goal with voyage-law-2...")
    embedder = LegalEmbedder()
    query_vec = embedder.get_voyage_query_vector(goal)

    print(f"[2/3] Hybrid search (top_k={top_k}, filename={filename or 'all'})...")
    response = search_hybrid(
        query_text=goal,
        query_vector=query_vec,
        top_k=top_k,
        org_id=org_id,
        workspace_id=workspace_id,
        specific_contract=filename,
    )
    results = response.get("results", [])
    metrics = response.get("reranker_metrics", {})
    print(
        f"    → {len(results)} chunks retrieved "
        f"(top_1_score={metrics.get('top_1_score', 0):.3f}, "
        f"spread={metrics.get('score_spread', 0):.3f})"
    )
    return results


async def _generate_brief(
    goal: str,
    chunks: list[dict],
) -> DecisionBriefResult:
    """Call Groq with structured output to produce the DecisionBriefResult."""
    from langchain_groq import ChatGroq

    print(f"[3/3] Calling Groq (llama-3.3-70b) for decision brief...")

    # ── Build context section from retrieved chunks ──────────────────────────
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("text", "")
        source = chunk.get("metadata", {}).get("source", "Unknown")
        page = chunk.get("metadata", {}).get("page", "?")
        clause_ref = chunk.get("metadata", {}).get("clause_reference", "")
        score = chunk.get("score", 0.0)
        header = f"[Chunk {i}] Source: {source}, Page {page}"
        if clause_ref:
            header += f", Ref: {clause_ref}"
        header += f" (relevance={score:.3f})"
        context_parts.append(f"{header}\n{text}")

    context_block = "\n\n---\n\n".join(context_parts)

    # ── Prompt ────────────────────────────────────────────────────────────────
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
- PREMISE CONFLICT RULE: If the retrieved context reveals that the goal references a wrong clause number, a non-existent provision, or a misidentified clause type (e.g. the goal says "termination notice in clause 15" but clause 15 is actually a severability clause), you MUST set proceed_recommended=false. The summary must lead with this conflict as sentence one — do not bury it. Do not recommend drafting on a faulty premise.
- relevance_reason must explain specifically how this clause affects the drafting strategy — not just that the topic appears in it. Bad: "termination is discussed here." Good: "defines the severance entitlements the response must acknowledge to avoid inadvertent waiver of Section 6 rights."
- verification_checklist items must be specific and actionable (e.g. "Confirm the notice was served on [Date] per Section 12.2") — not generic platitudes like "review the contract"
- summary must be 2-3 sentences maximum. Lead with the most important finding — if there is a premise conflict, that is always sentence one. Do not hedge. Write as if briefing a senior partner who has 20 seconds to read it.

GOAL: {goal}

RETRIEVED CONTRACT CONTEXT:
{context_block}

Generate the DecisionBriefResult now."""

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.0,
        api_key=os.getenv("GROQ_API_KEY", ""),
    ).with_structured_output(DecisionBriefResult)

    result: DecisionBriefResult = await llm.ainvoke(prompt)
    return result


# ── Pretty printer ──────────────────────────────────────────────────────────────

def _print_brief(brief: DecisionBriefResult, chunks: list[dict]) -> None:
    W = 72
    sep = "─" * W

    def wrap(text: str, indent: int = 4) -> str:
        prefix = " " * indent
        return textwrap.fill(text, width=W, initial_indent=prefix, subsequent_indent=prefix)

    print(f"\n{'═' * W}")
    print(f"  DECISION BRIEF")
    print(f"{'═' * W}")
    print(f"\n  GOAL: {brief.goal}")
    print(f"\n  CONFIDENCE: {brief.confidence:.0%}   PROCEED: {'✅ YES' if brief.proceed_recommended else '❌ NO'}")
    print(wrap(f"Reasoning: {brief.proceed_reasoning}"))

    print(f"\n{sep}")
    print("  SUMMARY")
    print(sep)
    print(wrap(brief.summary, indent=2))

    print(f"\n{sep}")
    print(f"  RECOMMENDED ACTIONS ({len(brief.recommended_actions)})")
    print(sep)
    for i, action in enumerate(brief.recommended_actions, 1):
        urgency_bar = "█" * int(action.urgency * 10) + "░" * (10 - int(action.urgency * 10))
        print(f"\n  [{i}] {action.action_type}  |  Urgency [{urgency_bar}] {action.urgency:.0%}")
        print(wrap(action.description))
        if action.deadline_date:
            print(f"      Deadline: {action.deadline_date}  (ref: {action.deadline_source or 'unknown'})")

    print(f"\n{sep}")
    print(f"  RELEVANT CLAUSES ({len(brief.relevant_clauses)})  ← grounding context for draft_node")
    print(sep)
    for i, clause in enumerate(brief.relevant_clauses, 1):
        print(f"\n  [{i}] {clause.clause_ref}  (doc: {clause.document_id[:8]}...)")
        print(wrap(f"Why: {clause.relevance_reason}"))
        print(wrap(f'Excerpt: "{clause.excerpt[:200]}{"..." if len(clause.excerpt) > 200 else ""}"', indent=6))

    # Grounding check: are the excerpts actually in the chunks?
    print(f"\n  ── Grounding Check ──")
    all_chunk_text = " ".join(c.get("text", "") for c in chunks).lower()
    grounding_pass = 0
    for clause in brief.relevant_clauses:
        # Check first 60 chars of excerpt against chunk corpus (whitespace-normalized)
        probe = clause.excerpt[:60].lower().replace("\n", " ").strip()
        found = probe in all_chunk_text
        mark = "✅" if found else "⚠️ NOT FOUND IN CHUNKS"
        print(f"    {mark}  \"{probe[:50]}...\"")
        if found:
            grounding_pass += 1
    grounding_pct = (grounding_pass / len(brief.relevant_clauses) * 100) if brief.relevant_clauses else 0
    print(f"\n  Grounding: {grounding_pass}/{len(brief.relevant_clauses)} clauses verified ({grounding_pct:.0f}%)")
    if grounding_pct < 80:
        print("  ⚠️  WARNING: Low grounding — brief may contain hallucinated excerpts. Do NOT proceed to draft.")

    if brief.conflicts_to_resolve:
        print(f"\n{sep}")
        print(f"  CONFLICTS TO RESOLVE ({len(brief.conflicts_to_resolve)})")
        print(sep)
        for i, conflict in enumerate(brief.conflicts_to_resolve, 1):
            print(f"\n  [{i}] {conflict.type}")
            print(wrap(conflict.description))
            print(f"      Source A: {conflict.source_a}")
            if conflict.source_b:
                print(f"      Source B: {conflict.source_b}")

    if brief.deadlines_implicated:
        print(f"\n{sep}")
        print(f"  DEADLINES IMPLICATED ({len(brief.deadlines_implicated)})")
        print(sep)
        for dl in brief.deadlines_implicated:
            print(f"  • {json.dumps(dl, default=str)}")

    print(f"\n{sep}")
    print(f"  VERIFICATION CHECKLIST ({len(brief.verification_checklist)} items)")
    print(sep)
    print("  These are the things the lawyer must confirm before drafting:\n")
    for i, item in enumerate(brief.verification_checklist, 1):
        print(wrap(f"{i}. {item}", indent=4))

    print(f"\n{'═' * W}\n")


# ── Entry point ─────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Test the Decision Brief node standalone.")
    parser.add_argument("--org-id",       required=True,  help="Org UUID for Qdrant tenant isolation")
    parser.add_argument("--workspace-id", required=True,  help="Workspace UUID for scoping")
    parser.add_argument("--goal",         required=True,  help='Goal text, e.g. "Draft a response to the termination notice in clause 15"')
    parser.add_argument("--filename",     default=None,   help="Scope retrieval to a single document filename")
    parser.add_argument("--top-k",        type=int, default=8, help="Number of chunks to retrieve (default: 8)")
    parser.add_argument("--out",          default=None,   help="Write JSON output to this file")
    args = parser.parse_args()

    # ── Run pipeline ────────────────────────────────────────────────────────
    chunks = _retrieve(
        goal=args.goal,
        org_id=args.org_id,
        workspace_id=args.workspace_id,
        filename=args.filename,
        top_k=args.top_k,
    )

    if not chunks:
        print("\n❌ No chunks retrieved. Check org_id, workspace_id, and that the document is indexed.")
        sys.exit(1)

    brief = await _generate_brief(goal=args.goal, chunks=chunks)

    # ── Print ────────────────────────────────────────────────────────────────
    _print_brief(brief, chunks)

    # ── Optionally write JSON ────────────────────────────────────────────────
    if args.out:
        with open(args.out, "w") as f:
            json.dump(brief.model_dump(mode="json"), f, indent=2, default=str)
        print(f"  Brief written to: {args.out}\n")


if __name__ == "__main__":
    asyncio.run(main())
