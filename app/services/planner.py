"""
ReAct Agent for Legal RAG
Replaces the deterministic classify→build→execute pipeline with a
Thought/Action/Observation loop that adapts based on tool outputs.
"""

# app/services/planner.py
import json
import logging
import re
from typing import Any, Dict, List

from app.services.legal_primitives import (
    draft_tool,
    logic_tool,
    read_tool,
    search_tool,
)

# Map tool names to functions
LEGAL_TOOLS = {
    "search_tool": search_tool,
    "read_tool": read_tool,
    "logic_tool": logic_tool,
    "draft_tool": draft_tool,
}


from app.services.legal_primitives import groq_client

logger = logging.getLogger(__name__)

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
            {"name": "search_tool", "params": {"top_k": 3}},  # Find relevant contract
            {"name": "read_tool", "params": {"target_fields": ["effective_date", "end_date", "termination_date", "parties"]}},
            {"name": "logic_tool", "params": {}},  # Uses extracted data
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


async def execute_plan(plan: Dict[str, Any], question: str, mode: str = "hybrid", file_ids: list = None, *, org_id: str) -> Dict[str, Any]:
    """
    Executes the static plan sequentially.
    Passes context between steps (Found chunks -> Read -> Logic -> Draft).
    file_ids: if provided, restricts search to those Qdrant file IDs (session scope).
    """
    logger.info("EXECUTING DETERMINISTIC PLAN: %s", plan['query_type'])
    
    context = {"chunks": [], "structured_data": {}}
    trace = []
    final_output = ""

    for step in plan["tools"]:
        tool_name = step["name"]
        params = step["params"].copy()
        logger.info("STEP: %s", tool_name)

        # --- DYNAMIC PARAMETER INJECTION ---
        
        # 1. Search Tool
        if tool_name == "search_tool":
            params["query"] = question
            params["mode"] = mode
            params["org_id"] = org_id  # Always scope by org, even without a session
            if file_ids:
                params["file_ids"] = file_ids
            result = await search_tool(**params)
            context["chunks"].extend(result)
            
            # Collect all unique contract sources from the chunks
            if result:
                sources = []
                for chunk in result:
                    match = re.search(r"\[Source: (.+?),", chunk)
                    if match and match.group(1) not in sources:
                        sources.append(match.group(1))
                
                context["current_contracts"] = sources
                if sources:
                    logger.info("Context: Focused on %d contracts: %s", len(sources), sources)

        # 2. Read Tool
        elif tool_name == "read_tool":
            # Needs contract names. If search found them, use them.
            contracts = context.get("current_contracts", [])
            if contracts:
                all_extracted = {}
                for contract in contracts:
                    params_copy = params.copy()
                    params_copy["contract_name"] = contract
                    params_copy["org_id"] = org_id  # Enforce org isolation in read_tool
                    res = await read_tool(**params_copy)
                    all_extracted[contract] = res
                
                context["structured_data"] = all_extracted
                # Add structured data to chunks for the drafter to see
                context["chunks"].append(f"Extracted Data: {json.dumps(all_extracted)}")
            else:
                logger.info("SKIPPED read_tool: No contracts identified from search results")
                continue

        # 3. Logic Tool
        elif tool_name == "logic_tool":
            # Needs extracted data + question
            data = context.get("structured_data")
            if data:
                # PAL Logic Tool: Passes data + question to LLM
                result = await logic_tool(data=data, question=question)
                # Add verdict to chunks
                context["chunks"].append(f"Logic Analysis: {result['verdict']} because {result['reasoning']}")
            else:
                logger.info("SKIPPED logic_tool: No structured data available")
                continue

        # 4. Draft Tool
        elif tool_name == "draft_tool":
            params["context_chunks"] = context["chunks"]
            params["original_question"] = question
            result = await draft_tool(**params)
            final_output = result

        trace.append({"step": tool_name, "output": "Executed"})

    return {
        "plan": plan,
        "context": context,
        "final_output": final_output,
        "trace": trace,
    }
