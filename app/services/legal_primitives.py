import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import AsyncGroq
from pydantic import BaseModel, Field

import re
from app.services.embedder import get_embedding
from app.services.store import (
    search_hybrid,
    search_hybrid_qdrant,  # Session-scoped Qdrant search
)

logger = logging.getLogger(__name__)


def _clean_json_output(raw: str) -> str:
    """Removes markdown code blocks commonly hallucinated by Qwen/Llama models around JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw


class Claim(BaseModel):
    statement: str = Field(
        description="A single factual statement answering a part of the user's prompt."
    )
    exact_quote: str = Field(
        description="The EXACT verbatim text from the search context that proves this statement. Do not paraphrase."
    )
    source_document: str = Field(
        description="The source document name and page number this quote came from."
    )


class FinalAnswer(BaseModel):
    claims_list: List[Claim] = Field(
        description="A list of facts extracted directly from the text."
    )
    synthesized_response: str = Field(
        description="A cohesive, natural language response built EXCLUSIVELY from the claims_list. Must include inline citations (filename.pdf, Page X)."
    )


load_dotenv()

groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))


async def generate_legal_concepts(question: str) -> List[str]:
    """
    Generates relevant legal terms of art, synonyms, and latin maxims.
    Used to bridge the vocabulary gap.
    """
    system_prompt = """
    You are a Senior Legal Research Assistant.
    Analyze the user's question and extract 3-5 specific legal concepts, terms of art, or keywords.

    Example:
    User: "What if they don't pay on time?"
    Output: ["late payment penalty", "interest on arrears", "event of default", "insolvency"]

    User: "Can I fire the contractor?"
    Output: ["termination for cause", "termination for convenience", "breach of contract", "notice period"]

    OUTPUT RULES:
    - Return ONLY a JSON list of strings
    - Do not repeat words from the question
    - Focus on formal legal terminology
    """

    response = await groq_client.chat.completions.create(
        model="qwen/qwen3-32b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    try:
        raw = response.choices[0].message.content if response.choices else "{}"
        raw = _clean_json_output(raw)
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            concepts = parsed.get("concepts") or parsed.get("list") or []
            if not concepts and parsed:
                concepts = next(iter(parsed.values()), [])
        else:
            concepts = parsed or []
        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]
        if not concepts:
            concepts = []
        logger.info("Legal concepts generated: %s", concepts)
        return concepts
    except Exception as e:
        logger.warning("Error parsing legal concepts: %s", e)
        return []


async def generate_multi_queries(question: str) -> List[str]:
    """
    Generates different phrasings/angles of the same question.
    Used to bridge the phrasing gap.
    """
    system_prompt = """
    You are a Legal AI.
    Generate 3 distinct variations of the user's question to maximize search recall.
    1. A formal version
    2. A specific scenario version
    3. A broad conceptual version

    OUTPUT ONLY a valid JSON object with a single key "queries" containing a list of strings.
    """

    response = await groq_client.chat.completions.create(
        model="qwen/qwen3-32b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},
        temperature=0.5,
    )

    try:
        raw = response.choices[0].message.content if response.choices else "{}"
        raw = _clean_json_output(raw)
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            queries = parsed.get("queries") or next(iter(parsed.values()), [])
        else:
            queries = parsed or []
        if isinstance(queries, str):
            queries = [q.strip() for q in queries.split(",") if q.strip()]
        logger.info("Multi-queries generated: %s", queries)
        return queries or [question]
    except Exception:
        return [question]


async def search_tool(
    query: str,
    specific_contracts: Optional[List[str]] = None,
    keyword_filter: Optional[str] = None,
    top_k: int = 5,
    mode: str = "hybrid",  # Options: hybrid, concept, multiquery
    file_ids: Optional[
        List[int]
    ] = None,  # Session scope: search only these Qdrant file IDs
    org_id: str,  # Required — no default to prevent accidental cross-tenant leakage
    **kwargs,  # Absorbs legacy params
) -> List[Dict]:
    """
    Finds relevant chunks using the specified retrieval strategy.
    Returns a list of rich result dicts: {"text", "score", "metadata": {"file_id", "source", "page"}}.

    Modes:
    - 'hybrid': Standard Vector + BM25 fusion
    - 'concept': Expands extra keywords -> joins them to query -> Hybrid Search
    - 'multiquery': Generates 3 questions -> Hybrid Search for each -> Pools results
    """
    if not org_id:
        raise ValueError("org_id must be provided for tenant isolation.")

    # --- STRATEGY SELECTION ---
    queries_to_run = []

    if mode == "multiquery":
        logger.info("Running multi-query search")
        variations = await generate_multi_queries(query)
        queries_to_run = [query] + variations[:2]  # original + 2 variations

    elif mode == "concept":
        logger.info("Running concept-expansion search")
        concepts = await generate_legal_concepts(query)
        expanded_query = f"{query} {' '.join(concepts)}"
        logger.info("Expanded query: %s", expanded_query)
        queries_to_run = [expanded_query]

    else:  # Default 'hybrid'
        queries_to_run = [query]

    # --- EXECUTION ---
    raw_results: List[List[Dict]] = []

    for q_text in queries_to_run:
        logger.info("Running hybrid search for query: %.80s", q_text)

        embeddings = get_embedding([q_text])
        if not embeddings:
            logger.warning("Embedding failed for query: %.50s — skipping", q_text)
            continue
        q_vector = embeddings[0]

        # Route: session-scoped (by file_ids) vs. org-wide
        if file_ids:
            logger.info("Session-scoped search: %d file(s)", len(file_ids))
            results = search_hybrid_qdrant(
                q_text, q_vector, file_ids=file_ids, org_id=org_id, top_k=top_k
            )
            raw_results.append(results)
        elif specific_contracts:
            for contract in specific_contracts:
                results = search_hybrid(
                    q_text,
                    q_vector,
                    top_k=top_k,
                    specific_contract=contract,
                    org_id=org_id,
                )
                raw_results.append(results)
        else:
            results = search_hybrid(q_text, q_vector, top_k=top_k, org_id=org_id)
            raw_results.append(results)

    # Flatten
    flat_results: List[Dict] = []
    for batch in raw_results:
        if isinstance(batch, list):
            flat_results.extend(batch)
        else:
            flat_results.append(batch)

    # Deduplicate by text
    unique_results: List[Dict] = []
    seen_texts: set = set()
    for item in flat_results:
        text = item.get("text", "")
        if text not in seen_texts:
            seen_texts.add(text)
            unique_results.append(item)

    # Sort by score and take top_k
    unique_results.sort(key=lambda x: x.get("score", 0), reverse=True)
    final_output = unique_results[:top_k]

    # Keyword post-filter (legacy support)
    if keyword_filter:
        logger.debug("Applying keyword filter: %s", keyword_filter)
        filtered = [r for r in final_output if keyword_filter.lower() in r.get("text", "").lower()]
        if filtered:
            final_output = filtered
        else:
            logger.warning("Keyword filter '%s' removed all results — returning unfiltered.", keyword_filter)

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
            model="qwen/qwen3-32b",
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


async def logic_tool(data: dict, question: str = "Is this contract currently valid?") -> dict:
    """
    Evaluates logical conditions using an LLM reasoning engine
    and returns a strictly formatted 'verdict' and 'reasoning'.
    """
    logger.info("logic_tool called | question=%.80s | data_keys=%s", question, list(data.keys()))

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
            model="qwen/qwen3-32b",
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

        logger.info("logic_tool result | verdict=%s | reasoning=%.80s", result["verdict"], result["reasoning"])
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
        output_format, use_cot, len(context_chunks),
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
    logger.debug("draft_tool: deduplicated %d -> %d chunks", len(context_chunks), len(unique_chunks))

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
        model="qwen/qwen3-32b",
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

    logger.info("draft_tool (simple): %d tokens used, %d chars output", tokens_used or 0, len(output))
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
    logger.debug("draft_tool: deduplicated %d -> %d chunks", len(context_chunks), len(unique_chunks))

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
        logger.warning("draft_tool: Pydantic AI failed after retries (%s) — falling back to simple mode", e)
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
