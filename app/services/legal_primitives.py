import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel, Field, create_model

from app.services.embedder import get_embedding
from app.services.store import (
    search_hybrid,
    search_hybrid_qdrant,  # Session-scoped Qdrant search
)

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def generate_legal_concepts(question: str) -> List[str]:
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

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )

    try:
        content = json.loads(response.choices[0].message.content)
        concepts = content.get("concepts", [])
        if not concepts and isinstance(content, list):
             concepts = content
        elif not concepts and "list" in content:
             concepts = content["list"]
             
        # Fallback if structure is weird but has keys
        if not concepts:
            concepts = list(content.values())[0] if content else []
        
        # Handle case where LLM returns a comma-separated string instead of a list
        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]
            
        print(f"\n🧠 CONCEPTS GENERATED: {concepts}")
        return concepts
    except Exception as e:
        print(f"⚠️ Error parsing concepts: {e}")
        return []


def generate_multi_queries(question: str) -> List[str]:
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

    OUTPUT ONLY a JSON list of strings.
    """

    response = groq_client.chat.completions.create(
        model="qwen/qwen3-32b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},
        temperature=0.5,
    )

    try:
        content = json.loads(response.choices[0].message.content)
        queries = list(content.values())[0] if content else []
        print(f"\n🧠 MULTI-QUERIES GENERATED: {queries}")
        return queries
    except:
        return [question]


def search_tool(
    query: str,
    specific_contracts: Optional[List[str]] = None,
    keyword_filter: Optional[str] = None,
    top_k: int = 5,
    mode: str = "hybrid",  # Options: hybrid, concept, multiquery
    file_ids: Optional[List[int]] = None,  # Session scope: search only these Qdrant file IDs
    org_id: str = "default_org",           # Required for Qdrant tenant isolation
    **kwargs,  # Absorbs legacy params
) -> List[str]:
    """
    Finds relevant chunks using the specified retrieval strategy.
    
    Modes:
    - 'hybrid': Standard Vector + BM25 fusion
    - 'concept': Expands extra keywords -> joins them to query -> Hybrid Search
    - 'multiquery': Generates 3 questions -> Hybrid Search for each -> Pools results
    """
    print("\n" + "=" * 70)
    print("🔍 SEARCH_TOOL CALLED")
    print("   Query:", query)
    print("   Mode:", mode)
    print("=" * 70)

    final_chunks = []
    
    # --- STRATEGY SELECTION ---
    
    queries_to_run = []
    
    if mode == "multiquery":
        print("\n📝 MULTI-QUERY MODE DETECTED")
        variations = generate_multi_queries(query)
        queries_to_run = [query] + variations[:2] # Run original + 2 variations
        
    elif mode == "concept":
        print("\n📝 CONCEPT EXPANSION MODE DETECTED")
        concepts = generate_legal_concepts(query)
        # Augment the query string with concepts for BM25 to catch
        expanded_query = f"{query} {' '.join(concepts)}"
        print(f"   ► Expanded Query: {expanded_query}")
        queries_to_run = [expanded_query]
        
    else: # Default 'hybrid'
        queries_to_run = [query]

    # --- EXECUTION ---
    
    raw_results = []
    seen_ids = set()
    
    for q_text in queries_to_run:
        print(f"\n   🏃 Running Hybrid Search for: '{q_text}'")
        
        # Embed the INDIVIDUAL query (for vector search)
        # Note: For 'concept' mode, we might want to embed the ORIGINAL query 
        # but search BM25 with EXPANDED. For simplicity, we embed the expanded one here.
        # This is a debated research topic. Embed-Expanded usually works fine if concepts are relevant.
        embeddings = get_embedding([q_text])
        if not embeddings:
            print(f"   ⚠️ Embedding failed for query: '{q_text[:50]}...' - Skipping")
            continue
        q_vector = embeddings[0]

        # ── Route: Qdrant (session-scoped) vs ChromaDB (all files) ───────────
        if file_ids:
            # Session is active — search only selected files in Qdrant
            print(f"   🔒 Session-scoped: searching {len(file_ids)} file(s) in Qdrant")
            results = search_hybrid_qdrant(q_text, q_vector, file_ids=file_ids, org_id=org_id, top_k=top_k)
            raw_results.append(results)
        elif specific_contracts:
            for contract in specific_contracts:
                results = search_hybrid(q_text, q_vector, top_k=top_k, specific_contract=contract)
                for r in results:
                    raw_results.append(r)
        else:
            results = search_hybrid(q_text, q_vector, top_k=top_k)
            raw_results.append(results)

    # Flatten logic if needed (search_hybrid returns list of dicts)
    flat_results = []
    
    # Regardless of which branch we took, `raw_results` is always a List[List[dict]] 
    # because we did raw_results.append(results) or raw_results.append(r) where `r` was a list of dicts.
    for batch in raw_results:
        if isinstance(batch, list):
            flat_results.extend(batch)
        else:
            flat_results.append(batch)

    # --- DEDUPLICATION (Simple text based) ---
    unique_results = []
    seen_texts = set()
    
    for item in flat_results:
        text = item.get("text", "")
        if text not in seen_texts:
            seen_texts.add(text)
            unique_results.append(item)
            
    # Sort by Score (Descending) logic if we mixed multiple queries
    # Since scores are RRF (roughly 0-1), they are comparable.
    unique_results.sort(key=lambda x: x["score"], reverse=True)
    
    # Take Top K
    final_output_objs = unique_results[:top_k]
    
    # Extract just text for return (keeping backward compatibility with original tool signature)
    final_chunks = [obj["text"] for obj in final_output_objs]

    # --- KEYWORD FILTERING (Legacy support) ---
    if keyword_filter:
        print(f"\n🔻 FILTERING: '{keyword_filter}'")
        filtered = [c for c in final_chunks if keyword_filter.lower() in c.lower()]
        if filtered:
            final_chunks = filtered
        else:
            print("   ⚠️ Filter removed everything. Returning original results.")

    print(f"\n✅ SEARCH COMPLETE: Returned {len(final_chunks)} chunks")
    return final_chunks


def read_tool(contract_name: str, target_fields: List[str]) -> Dict[str, Any]:
    """Extracts structured data (dates, parties, amounts) from a contract.
    Uses Hybrid Search (Vector + BM25) and Structured Outputs."""

    print(f"\n{'=' * 70}")
    print("📄 READ_TOOL CALLED (Structured Output Mode)")
    print(f"   Contract: {contract_name}")
    print(f"   Target Fields: {target_fields}")
    print(f"{'=' * 70}")

    query_text = "Keywords: " + ", ".join(target_fields)
    
    embeddings = get_embedding([query_text])
    if not embeddings:
        print(f"\n❌ EMBEDDING FAILED: Could not embed target fields")
        return {"error": "Failed to generate search embeddings"}
        
    chunks = search_hybrid(query_text, embeddings[0], top_k=10, specific_contract=contract_name)

    if not chunks:
        print(f"\n❌ CONTRACT NOT FOUND: {contract_name}")
        return {"error": f"Contract {contract_name} not found in database"}

    print(f"   ✓ Retrieved {len(chunks)} chunks")

    context = "\n\n".join(c["text"] if isinstance(c, dict) else c for c in chunks)
    max_chars = 12000
    if len(context) > max_chars:
        context = context[:max_chars] + "\n\n[...truncated for length...]"

    field_definitions = {field: (Optional[str], Field(default=None)) for field in target_fields}
    ExtractionModel = create_model("ExtractionModel", **field_definitions)

    schema_dict = ExtractionModel.model_json_schema()

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
        response = groq_client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract these fields: {', '.join(target_fields)}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        raw_output = response.choices[0].message.content
        extracted = json.loads(raw_output)

        print("   ✅ EXTRACTION SUCCESSFUL")
        for field, value in extracted.items():
            print(f"   • {field}: {value}")

        return extracted

    except Exception as e:
        print(f"\n❌ READ_TOOL FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"error": f"Extraction failed: {str(e)}"}


class LogicResult(BaseModel):
    verdict: str = Field(description="One of exactly: 'VALID', 'INVALID', or 'UNDETERMINED'")
    reasoning: str = Field(description="A terse string explaining why the verdict was reached based on the data")

def logic_tool(data: dict, question: str = "Is this contract currently valid?") -> dict:
    """
    Evaluates logical conditions using an LLM reasoning engine 
    and returns a strictly formatted 'verdict' and 'reasoning'.
    """
    print(f"\n{'=' * 70}")
    print("🧠 LOGIC_TOOL (Structured Reasoning Mode)")
    print(f"   Question: {question}")
    print(f"   Data keys: {list(data.keys())}")
    print(f"{'=' * 70}")

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
        response = groq_client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": "You are a logical deduction engine."},
                {"role": "user", "content": code_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        
        raw_output = response.choices[0].message.content
        result = json.loads(raw_output)
        result["code_used"] = None
        
        print(f"\n✅ LOGIC EVALUATION RESULT:")
        print(f"   Verdict: {result['verdict']}")
        print(f"   Reasoning: {result['reasoning']}")

        return result

    except Exception as e:
        print(f"   ❌ Logic evaluation failed: {e}")
        return {
            "verdict": "UNDETERMINED",
            "reasoning": f"Logic reasoning failed: {str(e)}",
            "code_used": None,
        }


def draft_tool(
    context_chunks: List[str],
    output_format: str = "prose",
    use_cot: bool = True,
    original_question: str = "",
) -> str:
    """Generates final output with optional CoT verification"""

    print(f"\n{'=' * 70}")
    print("✍️ DRAFT_TOOL CALLED")
    print(f"   Original Question: {original_question}")
    print(f"   Output Format: {output_format}")
    print(f"   Use CoT: {use_cot}")  # ADDED f
    print(f"   Input Chunks: {len(context_chunks)}")  # ADDED f
    print(f"{'=' * 70}")

    if use_cot:
        return _draft_with_cot(context_chunks, output_format, original_question)
    else:
        return _draft_simple(context_chunks, original_question, output_format)


def _draft_simple(
    context_chunks: List[str], original_question: str, output_format: str
) -> str:
    """Simple draft without CoT"""
    print("\n📝 SIMPLE DRAFT MODE")

    unique_chunks = list(set(context_chunks))
    print(f"   Deduplicated: {len(context_chunks)} → {len(unique_chunks)} chunks")

    context_text = "\n\n".join(unique_chunks)
    print(f"   Context length: {len(context_text)} chars")

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

    print("\n🤖 Calling OpenAI API (simple mode)...")
    print("   Model: gpt-4o-mini")
    print("   Temperature: 0")

    user_message = f"USER QUESTION: {original_question}\n\nBased strictly on the provided context, answer the question above."
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
    )

    output = response.choices[0].message.content
    tokens_used = response.usage.total_tokens

    print("\n✅ API Response Received")
    print(f"   Tokens used: {tokens_used}")
    print(f"   Output length: {len(output)} chars")
    print(f"   First 200 chars: {output[:200]}...")

    return output


def _draft_with_cot(
    context_chunks: List[str], output_format: str, original_question: str
) -> str:
    """Draft with Chain of Thought verification"""
    print("\n🧠 COT DRAFT MODE")

    # Ensure all chunks are strings and deduplicate (order-preserving)
    seen = set()
    unique_chunks = []
    for chunk in context_chunks:
        chunk_str = str(chunk) if not isinstance(chunk, str) else chunk
        if chunk_str not in seen:
            seen.add(chunk_str)
            unique_chunks.append(chunk_str)
    print(f"   Deduplicated: {len(context_chunks)} → {len(unique_chunks)} chunks")

    context_text = "\n\n".join(unique_chunks)
    print(f"   Context length: {len(context_text)} chars")

    format_instruction = _get_format_instruction(output_format)

    system_prompt = f"""
    You are a Senior Legal Analyst who produces precise, citation-rich answers.

    USER'S QUESTION: {original_question}
    
    STEP 1 - THINKING (Internal Verification):
    <thinking>
    - Analyze the user's request
    - Scan context for the EXACT contractual language that answers the question
    - For each claim you plan to make, identify:
      * The VERBATIM text from the contract (quote it exactly)
      * Source filename
      * Page number
    - Example: "The contract defines 'Cause' as: 'willful misconduct, material breach...' → Source: employment.pdf, Page 4"
    - DISCARD any claims without page numbers
    - DISCARD any generic legal knowledge not from the context
    - Draft the answer structure
    </thinking>

    STEP 2 - OUTPUT (Client-Facing):
    <output>

    [Your final formatted response that DIRECTLY answers: {original_question}]
    </output>

    IF YOU DO NOT USE THESE TAGS YOUR RESPONSE WILL BE REJECTED.
    
    ANSWER QUALITY RULES:
    - QUOTE exact contract language using quotation marks: "verbatim text from contract"
    - Do NOT paraphrase or summarize when exact language is available
    - Do NOT use generic phrases like "generally includes", "typically", "usually"
    - Every claim MUST cite (filename.pdf, Page X)
    - If a term is defined in the contract, quote the full definition
    - If asked about a clause, include the key provision text
    - Stay focused on answering: {original_question}
    - Use ONLY provided context — absolutely no external knowledge
    - If context empty/irrelevant: "Information not found in provided documents"

    {format_instruction}

    CONTEXT:
    {context_text}
    """
    user_message = (
        "You MUST structure your response using <thinking> tags for internal verification "
        "and <output> tags for the final client-facing response. "
        "Generate the verified report that directly answers the user's question."
    )
    print("\n🤖 Calling Groq API (Qwen 2.5 CoT mode)...")
    print("   Temperature: 0")
    print(f"   System prompt length: {len(system_prompt)} chars")

    response = groq_client.chat.completions.create(
        model="qwen/qwen3-32b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
        max_completion_tokens=4000
    )

    full_response = response.choices[0].message.content
    tokens_used = response.usage.total_tokens

    print("\n✅ API Response Received")
    print(f"   Tokens used: {tokens_used}")
    print(f"   Full response length: {len(full_response)} chars")
    print("\n📄 RAW RESPONSE (first 500 chars):")
    print(f"{full_response[:500]}")
    print(f"{'.' * 70}")

    # Parse thinking and output
    print("\n🔍 PARSING RESPONSE:")

    thinking_match = None
    output_match = None
    if full_response is not None:
        thinking_match = re.search(
            r"<thinking>(.*?)</thinking>", full_response, re.DOTALL | re.IGNORECASE
        )
        output_match = re.search(
            r"<output>(.*?)</output>", full_response, re.DOTALL | re.IGNORECASE
        )

    if thinking_match:
        thinking = thinking_match.group(1).strip()
        print(f"   ✓ Found <thinking> block ({len(thinking)} chars)")
        print("\n🧠 THINKING CONTENT:")
        print(f"{'-' * 70}")
        print(f"{thinking}")
        print(f"{'-' * 70}\n")
    else:
        print("   ❌ No <thinking> tags found!")

    if output_match:
        final_output = output_match.group(1).strip()
        print(f"   ✓ Found <output> block ({len(final_output)} chars)")

        # Count citations - support multiple formats:
        # Format 1: (filename.pdf, Page X) — strict
        # Format 2: Page X — loose (in tables, the filename may be in a different column)
        strict_citations = re.findall(r"\([^)]*\.pdf,\s*Page\s*\d+\)", final_output)
        loose_citations = re.findall(r"Page\s*\d+", final_output)
        citations = strict_citations if strict_citations else loose_citations
        print(f"   ✓ Citations found: {len(citations)} ({'strict' if strict_citations else 'loose'})")

        if citations:
            print(f"   Citations: {citations}")

        print("\n✅ CoT PARSING SUCCESSFUL")
        print("=" * 70 + "\n")
        return final_output
    else:
        print("   ❌ No <output> tags found!")

        # Try fallback
        if "<output>" in full_response:
            print("   ⚠️ Found unclosed <output> tag, extracting partial...")
            partial = full_response.split("<output>", 1)[1]
            print(f"   Partial length: {len(partial)} chars")
            return partial.strip()
        else:
            print("   ⚠️ Returning full response (no tags at all)")
            print("=" * 70 + "\n")
            return full_response


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


# Tool registry unchanged
LEGAL_TOOLS = {
    "search_tool": {"function": search_tool},
    "read_tool": {"function": read_tool},
    "logic_tool": {"function": logic_tool},
    "draft_tool": {"function": draft_tool},
}
