"""
Deterministic Planner for Legal RAG.
Classifies query intent, builds a tool execution plan, and runs it sequentially.
Context (chunks, file IDs, structured data) is passed between steps.
"""

# app/services/planner.py
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.services.legal_primitives import (
    draft_tool,
    logic_tool,
    read_tool,
    search_tool,
)

logger = logging.getLogger(__name__)

# Map tool names to functions
LEGAL_TOOLS = {
    "search_tool": search_tool,
    "read_tool": read_tool,
    "logic_tool": logic_tool,
    "draft_tool": draft_tool,
}

from app.services.legal_primitives import groq_client


async def _classify_intent(question: str) -> str:
    """LLM-based classification to route query to the right toolchain"""

    system_prompt = """You are a legal intent classifier.
Analyze the user's question and classify it into EXACTLY ONE of these three categories:
1. "logic_check": Questions about validity, expiration, termination, active dates, or if a contract is currently in force.
2. "extraction": Questions asking to list, extract, or identify specific entities, parties, people, companies, fees, salaries, or numbers.
3. "search": Broad questions asking what a contract says about a topic, summaries, or general research.

OUTPUT FORMAT:
You must output strictly valid JSON matching this schema:
{"intent": "logic_check" | "extraction" | "search"}
"""

    try:
        response = await groq_client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        raw_output = response.choices[0].message.content
        result = json.loads(raw_output)
        intent = result.get("intent", "search")
        if intent not in ["logic_check", "extraction", "search"]:
            intent = "search"
        logger.info("Intent classified as: %s", intent)
        return intent

    except Exception as e:
        logger.warning("Intent classification failed, defaulting to 'search': %s", e)
        return "search"


async def create_execution_plan(question: str) -> Dict[str, Any]:
    """
    Deterministic Planner.
    Returns a static list of tools to execute based on intent.
    """
    intent = await _classify_intent(question)
    tools = []

    if intent == "logic_check":
        tools = [
            {"name": "search_tool", "params": {"top_k": 3}},
            {"name": "read_tool", "params": {"target_fields": ["effective_date", "end_date", "termination_date", "parties"]}},
            {"name": "logic_tool", "params": {}},
            {"name": "draft_tool", "params": {"output_format": "prose"}},
        ]
    elif intent == "extraction":
        tools = [
            {"name": "search_tool", "params": {"top_k": 3}},
            {"name": "read_tool", "params": {"target_fields": ["fees", "compensation", "names", "parties", "entities", "people", "companies"]}},
            {"name": "draft_tool", "params": {"output_format": "prose"}},
        ]
    else:  # Search (default)
        tools = [
            {"name": "search_tool", "params": {"top_k": 5}},
            {"name": "draft_tool", "params": {"output_format": "prose"}},
        ]

    return {
        "query_type": intent,
        "tools": tools,
        "reasoning": f"Classified as {intent} based on keywords.",
    }


async def execute_plan(
    plan: Dict[str, Any],
    question: str,
    mode: str = "hybrid",
    file_ids: Optional[List[int]] = None,
    *,
    org_id: str,
) -> Dict[str, Any]:
    """
    Executes the static plan sequentially.
    Passes context (chunks, file_ids, structured_data) between steps.
    file_ids: if provided, restricts search to those Qdrant file IDs (session scope).
    """
    logger.info("EXECUTING PLAN: %s | mode=%s | scoped_files=%s", plan["query_type"], mode, len(file_ids) if file_ids else "all")

    context: Dict[str, Any] = {
        "chunks": [],          # List[str] — text for the drafter
        "result_objects": [],  # List[Dict] — rich objects from search (includes file_id)
        "structured_data": {},
        "file_ids_found": [],  # file_ids extracted from search results
        "filenames_found": [], # filenames for display / logging
    }
    trace = []
    final_output = ""

    for step in plan["tools"]:
        tool_name = step["name"]
        params = step["params"].copy()
        logger.info("EXECUTING STEP: %s", tool_name)

        # --- DYNAMIC PARAMETER INJECTION ---

        if tool_name == "search_tool":
            params["query"] = question
            params["mode"] = mode
            params["org_id"] = org_id
            if file_ids:
                params["file_ids"] = file_ids

            result_objects: List[Dict] = await search_tool(**params)

            # Extract text for the drafter
            context["chunks"].extend(r["text"] for r in result_objects)
            context["result_objects"].extend(result_objects)

            # Collect unique file_ids and filenames for the read_tool
            for r in result_objects:
                meta = r.get("metadata", {})
                fid = meta.get("file_id")
                fname = meta.get("source", "")
                if fid and fid not in context["file_ids_found"]:
                    context["file_ids_found"].append(fid)
                if fname and fname not in context["filenames_found"]:
                    context["filenames_found"].append(fname)

            trace.append({
                "step": tool_name,
                "chunks_found": len(result_objects),
                "file_ids": context["file_ids_found"],
                "filenames": context["filenames_found"],
            })
            logger.info(
                "search_tool: found %d chunks from files: %s",
                len(result_objects),
                context["filenames_found"],
            )

        elif tool_name == "read_tool":
            # Use the real file_ids surfaced by search, not fragile regex on filename strings
            found_file_ids = context.get("file_ids_found", [])
            if found_file_ids:
                params_copy = params.copy()
                params_copy["file_ids"] = found_file_ids
                params_copy["org_id"] = org_id
                params_copy["filenames"] = context.get("filenames_found", [])

                extracted = await read_tool(**params_copy)
                context["structured_data"] = extracted
                context["chunks"].append(f"Extracted Data: {json.dumps(extracted)}")

                trace.append({
                    "step": tool_name,
                    "file_ids_used": found_file_ids,
                    "fields_extracted": list(extracted.keys()) if isinstance(extracted, dict) else [],
                })
            else:
                logger.info("SKIPPED read_tool: no file_ids found in search results")
                trace.append({"step": tool_name, "skipped": True, "reason": "No file_ids from search"})
                continue

        elif tool_name == "logic_tool":
            data = context.get("structured_data")
            if data and not data.get("error"):
                result = await logic_tool(data=data, question=question)
                context["chunks"].append(f"Logic Analysis: {result['verdict']} — {result['reasoning']}")
                trace.append({
                    "step": tool_name,
                    "verdict": result.get("verdict"),
                    "reasoning": result.get("reasoning", "")[:200],
                })
            else:
                logger.info("SKIPPED logic_tool: no structured data available")
                trace.append({"step": tool_name, "skipped": True, "reason": "No structured data"})
                continue

        elif tool_name == "draft_tool":
            params["context_chunks"] = context["chunks"]
            params["original_question"] = question
            result = await draft_tool(**params)
            final_output = result
            trace.append({
                "step": tool_name,
                "output_chars": len(final_output),
            })

    return {
        "plan": plan,
        "context": context,
        "final_output": final_output,
        "trace": trace,
    }
