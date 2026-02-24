"""
ReAct Agent for Legal RAG
Replaces the deterministic classify→build→execute pipeline with a
Thought/Action/Observation loop that adapts based on tool outputs.
"""

# app/services/planner.py
import json
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

def _classify_intent(question: str) -> str:
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
        response = groq_client.chat.completions.create(
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
        print(f"⚠️ Intent classification failed, defaulting to 'search': {e}")
        return "search"


def create_execution_plan(question: str) -> Dict[str, Any]:
    """
    Deterministic Planner.
    Returns a static list of tools to execute based on intent.
    """
    intent = _classify_intent(question)
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


def execute_plan(plan: Dict[str, Any], question: str, mode: str = "hybrid", file_ids: list = None, org_id: str = "default_org") -> Dict[str, Any]:
    """
    Executes the static plan sequentially.
    Passes context between steps (Found chunks -> Read -> Logic -> Draft).
    file_ids: if provided, restricts search to those Qdrant file IDs (session scope).
    """
    print(f"\n🚀 EXECUTING DETERMINISTIC PLAN: {plan['query_type']}")
    
    context = {"chunks": [], "structured_data": {}}
    trace = []
    final_output = ""

    for step in plan["tools"]:
        tool_name = step["name"]
        params = step["params"].copy()
        print(f"\n▶ STEP: {tool_name}")

        # --- DYNAMIC PARAMETER INJECTION ---
        
        # 1. Search Tool
        if tool_name == "search_tool":
            params["query"] = question
            params["mode"] = mode
            # Pass session scope if available
            if file_ids:
                params["file_ids"] = file_ids
                params["org_id"] = org_id
            result = search_tool(**params)
            context["chunks"].extend(result)
            
            # Heuristic: If we found a specific contract source, use it for read_tool
            # (Simple: just pick the first source found)
            if result:
                first_source = result[0].split("[Source: ")[1].split(",")[0].strip()
                context["current_contract"] = first_source
                print(f"   Context: Focused on {first_source}")

        # 2. Read Tool
        elif tool_name == "read_tool":
            # Needs a contract name. If search found one, use it.
            contract = context.get("current_contract")
            if contract:
                params["contract_name"] = contract
                result = read_tool(**params)
                context["structured_data"] = result
                # Add structured data to chunks for the drafter to see
                context["chunks"].append(f"Extracted Data: {json.dumps(result)}")
            else:
                print("   ⚠ SKIPPED: No contract identified from search results")
                continue

        # 3. Logic Tool
        elif tool_name == "logic_tool":
            # Needs extracted data + question
            data = context.get("structured_data")
            if data:
                # PAL Logic Tool: Passes data + question to LLM
                result = logic_tool(data=data, question=question)
                # Add verdict to chunks
                context["chunks"].append(f"Logic Analysis: {result['verdict']} because {result['reasoning']}")
            else:
                print("   ⚠ SKIPPED: No structured data available")
                continue

        # 4. Draft Tool
        elif tool_name == "draft_tool":
            params["context_chunks"] = context["chunks"]
            params["original_question"] = question
            result = draft_tool(**params)
            final_output = result

        trace.append({"step": tool_name, "output": "Executed"})

    return {
        "plan": plan,
        "context": context,
        "final_output": final_output,
        "trace": trace,
    }
