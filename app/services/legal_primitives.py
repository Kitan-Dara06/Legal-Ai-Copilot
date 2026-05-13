import json
import logging
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import AsyncGroq
from pydantic import BaseModel, Field

from app.services.embedder import get_embedding
from app.services.store import (
    search_hybrid,
    search_hybrid_qdrant,  # Session-scoped Qdrant search
)

logger = logging.getLogger(__name__)

load_dotenv()
groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))


async def search_tool(
    query: str,
    specific_contracts: Optional[List[str]] = None,
    keyword_filter: Optional[str] = None,
    top_k: int = 5,
    mode: str = "hybrid",
    file_ids: Optional[List[int]] = None,
    *,
    org_id: str,
    **kwargs,
) -> List[Dict]:
    """
    Hybrid search over Qdrant (dense + sparse + RRF fusion).
    Returns list of dicts: {"text", "score", "metadata": {"file_id", "source", "page"}}.
    """
    if not org_id:
        raise ValueError("org_id must be provided for tenant isolation.")

    logger.info("Running hybrid search for query: %.80s", query)

    embeddings = get_embedding([query])
    if not embeddings:
        logger.warning("Embedding failed for query: %.50s — skipping", query)
        return []
    q_vector = embeddings[0]

    if file_ids:
        logger.info("Session-scoped search: %d file(s)", len(file_ids))
        results = search_hybrid_qdrant(
            query, q_vector, file_ids=file_ids, org_id=org_id, top_k=top_k
        )
    elif specific_contracts:
        results = []
        for contract in specific_contracts:
            batch = search_hybrid(
                query, q_vector, top_k=top_k, specific_contract=contract, org_id=org_id
            )
            if isinstance(batch, dict):
                results.extend(batch.get("results", batch))
            elif isinstance(batch, list):
                results.extend(batch)
    else:
        results = search_hybrid(query, q_vector, top_k=top_k, org_id=org_id)

    # Normalize return format
    if isinstance(results, dict):
        results = results.get("results", results)
    if not isinstance(results, list):
        results = []

    # Deduplicate by text
    seen: set = set()
    unique = []
    for item in results:
        text = item.get("text", "")
        if text and text not in seen:
            seen.add(text)
            unique.append(item)

    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    final_output = unique[:top_k]

    # Keyword post-filter
    if keyword_filter:
        filtered = [
            r
            for r in final_output
            if keyword_filter.lower() in r.get("text", "").lower()
        ]
        if filtered:
            final_output = filtered

    logger.info("Search complete: %d chunks returned", len(final_output))
    return final_output


def _build_extraction_schema(target_fields: List[str]) -> Dict[str, Any]:
    properties = {
        field: {"type": "string", "nullable": True} for field in target_fields
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
    }


async def read_tool(
    file_ids: List[int],
    target_fields: List[str],
    org_id: str,
    filenames: Optional[List[str]] = None,  # For display purposes only
) -> Dict[str, Any]:
    """Extracts structured data (dates, parties, amounts) from scoped files.
    Uses Hybrid Search (Vector + BM25) filtered by file_id for precise tenant isolation.
    """

    if not org_id:
        raise ValueError("org_id is required for tenant isolation.")

    if not file_ids:
        logger.warning("read_tool called with empty file_ids — skipping")
        return {"error": "No file_ids provided"}

    query_text = "Keywords: " + ", ".join(target_fields)

    embeddings = get_embedding([query_text])
    if not embeddings:
        logger.error("read_tool: Embedding failed for target fields")
        return {"error": "Failed to generate search embeddings"}

    # Search scoped to the specific file IDs from the session
    from app.services.store import search_hybrid_qdrant

    results = search_hybrid_qdrant(
        query_text,
        embeddings[0],
        file_ids=file_ids,
        org_id=org_id,
        top_k=10,
    )

    if not results:
        label = ", ".join(filenames) if filenames else f"file_ids={file_ids}"
        logger.warning("read_tool: No results for %s", label)
        return {"error": f"No content found for files: {label}"}

    logger.debug("read_tool: Retrieved %d chunks", len(results))

    context = "\n\n".join(r["text"] if isinstance(r, dict) else r for r in results)
    max_chars = 12000
    if len(context) > max_chars:
        context = context[:max_chars] + "\n\n[...truncated for length...]"

    schema_dict = _build_extraction_schema(target_fields)

    system_prompt = f"""You are a legal data extraction specialist.

Extract ONLY these exact fields from the contract based strictly on the provided context.

OUTPUT RULES:
- You MUST output strictly valid JSON matching exactly this schema: {json.dumps(schema_dict)}
- If a field is not found, set it to null (do NOT omit the field)
- Be precise - extract exactly what's in the document

FORMAT RULES:
- Dates: Use YYYY-MM-DD format (e.g., "2024-03-15")
- Money: Include currency symbol and amount (e.g., "$50,000" or "50000 USD")
- Lists: Return comma-separated strings
- Text: Keep original wording from contract

CONTRACT TEXT:
{context}
"""
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Extract these fields: {', '.join(target_fields)}",
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        raw_output = response.choices[0].message.content if response.choices else "{}"
        extracted = json.loads(raw_output or "{}")

        logger.info("read_tool extraction successful: %s", list(extracted.keys()))
        return extracted

    except Exception as e:
        logger.exception("read_tool failed: %s", e)
        return {"error": f"Extraction failed: {str(e)}"}


class LogicResult(BaseModel):
    verdict: str = Field(
        description="One of exactly: 'VALID', 'INVALID', or 'UNDETERMINED'"
    )
    reasoning: str = Field(
        description="A terse string explaining why the verdict was reached based on the data"
    )


async def logic_tool(
    data: dict, question: str = "Is this contract currently valid?"
) -> dict:
    """
    Evaluates logical conditions using an LLM reasoning engine
    and returns a strictly formatted 'verdict' and 'reasoning'.
    """
    logger.info(
        "logic_tool called | question=%.80s | data_keys=%s", question, list(data.keys())
    )

    code_prompt = f"""You are a legal reasoning engine.
Given contract data and a question, determine the answer.

CONTRACT DATA:
{json.dumps(data, indent=2, default=str)}

QUESTION: {question}
TODAY'S DATE: {date.today().isoformat()}

Analyze the data to answer the question. If the data is empty or missing required fields, output "UNDETERMINED".
You MUST output strictly valid JSON matching this schema:
{{"verdict": "VALID/INVALID/UNDETERMINED", "reasoning": "Explanation string"}}
"""

    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a logical deduction engine."},
                {"role": "user", "content": code_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        raw_output = response.choices[0].message.content if response.choices else "{}"
        parsed = json.loads(raw_output or "{}") if raw_output else {}
        result = parsed if isinstance(parsed, dict) else {}
        result.setdefault("verdict", "UNDETERMINED")
        result.setdefault("reasoning", "No reasoning provided.")
        result["code_used"] = None

        logger.info(
            "logic_tool result | verdict=%s | reasoning=%.80s",
            result["verdict"],
            result["reasoning"],
        )
        return result

    except Exception as e:
        logger.exception("logic_tool failed: %s", e)
        return {
            "verdict": "UNDETERMINED",
            "reasoning": f"Logic reasoning failed: {str(e)}",
            "code_used": None,
        }


async def draft_tool(
    context_chunks: List[str],
    output_format: str = "prose",
    use_cot: bool = True,
    original_question: str = "",
) -> str:
    """Generates final output with optional CoT verification"""

    logger.info(
        "draft_tool called | format=%s | use_cot=%s | chunks=%d",
        output_format,
        use_cot,
        len(context_chunks),
    )

    if use_cot:
        return await _draft_with_cot(context_chunks, output_format, original_question)
    else:
        return await _draft_simple(context_chunks, original_question, output_format)


async def _draft_simple(
    context_chunks: List[str], original_question: str, output_format: str
) -> str:
    """Simple draft without CoT"""
    logger.debug("draft_tool: simple mode")

    unique_chunks = list(set(context_chunks))
    logger.debug(
        "draft_tool: deduplicated %d -> %d chunks",
        len(context_chunks),
        len(unique_chunks),
    )

    context_text = "\n\n".join(unique_chunks)

    format_instruction = _get_format_instruction(output_format)

    system_prompt = f""" You are a Legal Analyst.

    RULES:
    1. Use ONLY provided context
    2. Every claim needs citation: (filename.pdf, Page X)
    3. If info missing, state it explicitly

    {format_instruction}

    CONTEXT:
    {context_text}
    """

    user_message = f"USER QUESTION: {original_question}\n\nBased strictly on the provided context, answer the question above."
    response = await groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )

    output = (response.choices[0].message.content or "") if response.choices else ""
    usage = getattr(response, "usage", None)
    tokens_used = (
        usage.total_tokens if usage and hasattr(usage, "total_tokens") else None
    )

    logger.info(
        "draft_tool (simple): %d tokens used, %d chars output",
        tokens_used or 0,
        len(output),
    )
    return output


async def _draft_with_cot(
    context_chunks: List[str], output_format: str, original_question: str
) -> str:
    """Draft with rigorous schema validation via Pydantic AI"""
    logger.info("draft_tool: pydantic faithfulness mode")

    # Ensure all chunks are strings and deduplicate (order-preserving)
    seen = set()
    unique_chunks = []
    for chunk in context_chunks:
        chunk_str = str(chunk) if not isinstance(chunk, str) else chunk
        if chunk_str not in seen:
            seen.add(chunk_str)
            unique_chunks.append(chunk_str)
    logger.debug(
        "draft_tool: deduplicated %d -> %d chunks",
        len(context_chunks),
        len(unique_chunks),
    )

    context_text = "\n\n".join(unique_chunks)

    format_instruction = _get_format_instruction(output_format)

    system_prompt = f"""
    You are a Senior Legal Analyst who produces precise, citation-rich answers.
    Your goal is to answer the user's question by FIRST extracting exact claims from the context, and THEN synthesizing a response.

    {format_instruction}
    """

    user_prompt = f"""
    USER QUESTION: {original_question}

    CONTEXT:
    {context_text}
    """

    from pydantic_ai import Agent
    from pydantic_ai.models.groq import GroqModel

    model = GroqModel("qwen/qwen3-32b")

    agent = Agent(
        model=model,
        output_type=FinalAnswer,
        system_prompt=system_prompt,
        retries=3,
    )

    try:
        result = await agent.run(user_prompt)
        logger.info(
            "draft_tool: Pydantic validation passed. %d claims extracted, %d chars output",
            len(result.output.claims_list),
            len(result.output.synthesized_response),
        )
        return result.output.synthesized_response

    except Exception as e:
        logger.warning(
            "draft_tool: Pydantic AI failed after retries (%s) — falling back to simple mode",
            e,
        )
        return await _draft_simple(unique_chunks, original_question, output_format)


def _get_format_instruction(output_format: str) -> str:
    """Helper to get format instructions"""
    instructions = {
        "table": "OUTPUT FORMAT: Markdown table with columns: | Contract | Finding | Citation |",
        "bullet_list": "OUTPUT FORMAT: Bullet points with citations. Format: - [Finding] (citation)",
        "email": "OUTPUT FORMAT: Professional email with Subject, Salutation, Body, Sign-off",
        "memo": "OUTPUT FORMAT: Legal memo with TO, FROM, RE, ISSUE, DISCUSSION, CONCLUSION",
        "prose": "OUTPUT FORMAT: Clear professional prose with inline citations (filename.pdf, Page X)",
    }
    return instructions.get(output_format, instructions["prose"])


# Tool registry
LEGAL_TOOLS = {
    "search_tool": {"function": search_tool},
    "read_tool": {"function": read_tool},
    "logic_tool": {"function": logic_tool},
    "draft_tool": {"function": draft_tool},
}
