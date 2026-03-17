# app/router/query.py
import os

import logging

from dotenv import load_dotenv
from fastapi import APIRouter, Query, Depends
from groq import Groq
from starlette.concurrency import run_in_threadpool

from app.dependencies import get_org_id_unified
from app.services.embedder import get_embedding
from app.services.store import (
    extract_sources_from_chunks,
    get_all_contract_names,
    search_hybrid,
)
from app.services.legal_primitives import search_tool

load_dotenv()

logger = logging.getLogger(__name__)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def generate_final_answer(question: str, context_chunks: list[str]):
    """
    Takes the user's question and the list of LABELED chunks from the DB,
    and produces a cited legal response.
    """

    context_text = "\n\n" + "=" * 30 + "\n\n".join(context_chunks) + "\n\n" + "=" * 30

    system_prompt = f"""
    You are a professional Legal Analyst. Your task is to provide a grounded,
    precise answer to a user's question based strictly on the provided context.

    RULES FOR YOUR RESPONSE:
    1. USE ONLY PROVIDED CONTEXT: Do not use outside legal knowledge.
    2. MANDATORY CITATIONS: Every claim you make must be followed by a citation
       pointing to the Source and Page Number provided in the context header.
    3. CITATION FORMAT: Use parentheses, e.g., (Filename.pdf, Page X).
    4. NO HALLUCINATIONS: If the context does not contain the answer, explicitly state
       that the information is not available in the provided documents.
    5. STRUCTURE: Use bullet points or a table if the user asks for comparisons.


    6. COMPARISON MODE: If the user asks to compare documents or lists multiple contracts,
           you MUST output the answer as a Markdown Table.

           Table Format Example:
           | Contract Name | Clause Type | Snippet | Citation |
           |---------------|-------------|---------|----------|
           | Vendor Agrmt  | Termination | 30 days notice... | (vendor.pdf, Page 4) |
           | Lease Agrmt   | Breach      | Immediate...      | (lease.pdf, Page 9)  |

        CONTEXT:
        {context_text}
    """

    try:
        response = client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Based on the documents provided, {question}",
                },
            ],
            temperature=0,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("Error generating final response: %s", e)
        return "I encountered an error while trying to synthesize the answer."


router = APIRouter()


@router.post("/ask")
async def ask(
    question: str,
    org_id: str = Depends(get_org_id_unified),
    mode: str = Query(default="hybrid", description="Search strategy: hybrid, concept, or multiquery"),
):
    """
    Main query endpoint.
    
    - mode=hybrid: Vector + BM25 keyword fusion (fast, balanced)
    - mode=concept: Expands legal terminology then hybrid search (better recall)
    - mode=multiquery: Rephrases question from 3 angles (broadest coverage)
    """
    # These calls use synchronous network clients; run them off the event loop.
    all_chunks = await run_in_threadpool(
        search_tool, query=question, mode=mode, top_k=5, org_id=org_id
    )
    final_answer = await run_in_threadpool(generate_final_answer, question, all_chunks)

    return {
        "original_question": question,
        "search_mode": mode,
        "context_chunks_used": len(all_chunks),
        "answer": final_answer,
    }
