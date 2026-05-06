"""
Defined Terms Extractor
=======================
LLM-assisted extraction of legal defined terms.
Stores results in `defined_terms_registry` via async SQLAlchemy.

Hierarchy pre-filter: Only chunks whose hierarchy contains definition-related
keywords ("Definition", "Definitions", "Glossary", "Interpretation") are sent
 to the LLM. All others are skipped.

Conflict detection is deferred to the workspace-wide sweep task
`resolve_defined_term_conflicts` -- no inline conflict checks here.
"""

import asyncio
import json
import os
import re
import traceback
import uuid
from typing import Dict, List

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import DefinedTermRegistry
from app.redis_client import acquire_llm_slot, release_llm_slot
from app.tasks import is_definition_chunk


class DefinedTermExtractor:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ).rstrip("/"),
        )
        self.model = "meta-llama/llama-3.1-8b-instruct"  # OpenRouter fallback

    async def extract_and_store(
        self,
        chunks: List[Dict],
        document_id: uuid.UUID,
        workspace_id: uuid.UUID,
        org_id: uuid.UUID,
    ):
        """Processes chunks concurrently, extracts definitions using Redis semaphore, upserts to PostgreSQL via async session."""
        print(
            f"[terms_extractor] Extracting defined terms from document {document_id}..."
        )

        terms_found = 0

        async def process_chunk(chunk, db):
            nonlocal terms_found
            text = chunk["text"]
            # Pre-filter: skip short or non-definition chunks
            if len(text) < 30:
                return
            if not is_definition_chunk(chunk):
                return

            path_str = " > ".join(chunk.get("hierarchy", []))
            page_num = chunk.get("page_number", 1)

            prompt = f"""Identify any explicitly "Defined Terms" in the following text.
These are usually capitalized and enclosed in quotes (e.g., "Confidential Information", "Seller").

You MUST output valid JSON in this exact structure:
{{"terms": [{{"term": "...", "definition": "..."}}]}}

If there are no defined terms, return: {{"terms": []}}

Text: {text}
"""
            # Acquire token bucket lease
            lease_id = None
            while True:
                lease_id = await acquire_llm_slot(str(org_id), max_slots=5)
                if lease_id:
                    break
                await asyncio.sleep(0.5)

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                raw = response.choices[0].message.content.strip()
                # Strip markdown fences if present
                raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
                parsed = json.loads(raw)

                for item in parsed.get("terms", []):
                    term_val = item.get("term", "").strip()
                    defn_val = item.get("definition", "").strip()
                    if not term_val or not defn_val:
                        continue

                    # NO inline conflict detection -- deferred to workspace sweep task
                    db.add(
                        DefinedTermRegistry(
                            workspace_id=workspace_id,
                            org_id=org_id,
                            term=term_val,
                            definition=defn_val,
                            source_document_id=document_id,
                            page=page_num,
                            clause_reference=path_str,
                            conflict_flag=False,
                            conflict_description=None,
                        )
                    )
                    terms_found += 1

            except Exception as e:
                error_path = path_str or "Unknown Path"
                print(
                    f"  \u26a0\ufe0f [terms_extractor] Extraction failed on [{error_path}]: {e}"
                )
                traceback.print_exc()
            finally:
                if lease_id:
                    await release_llm_slot(str(org_id), lease_id)

        async with AsyncSessionLocal() as db:
            # Gather chunk executions concurrently
            await asyncio.gather(*(process_chunk(chunk, db) for chunk in chunks))
            await db.commit()

        print(
            f"[terms_extractor] ✓ Stored {terms_found} defined terms for document {document_id}."
        )
