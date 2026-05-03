"""
Defined Terms Extractor
=======================
LLM-assisted extraction of legal defined terms.
Stores results in `defined_terms_registry` via async SQLAlchemy.
"""

import json
import os
import re
import uuid
import traceback
from typing import Dict, List
import asyncio

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DefinedTermRegistry
from app.database import AsyncSessionLocal

class DefinedTermExtractor:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        )
        self.model = "meta-llama/llama-3.1-8b-instruct"  # OpenRouter fallback

    async def extract_and_store(
        self, 
        chunks: List[Dict], 
        document_id: uuid.UUID, 
        workspace_id: uuid.UUID, 
        org_id: uuid.UUID
    ):
        """Processes chunks, extracts definitions, upserts to PostgreSQL via async session."""
        print(f"[terms_extractor] Extracting defined terms from document {document_id}...")

        terms_found = 0
        async with AsyncSessionLocal() as db:
            for chunk in chunks:
                text = chunk["text"]
                # Skip chunks that are likely too short to contain definitions
                if len(text) < 30:
                    continue
                    
                path_str = " > ".join(chunk.get("hierarchy", []))
                page_num = chunk.get("page_number", 1)

                prompt = f"""Identify any explicitly "Defined Terms" in the following text.
These are usually capitalized and enclosed in quotes (e.g., "Confidential Information", "Seller").

You MUST output valid JSON in this exact structure:
{{"terms": [{{"term": "...", "definition": "..."}}]}}

If there are no defined terms, return: {{"terms": []}}

Text: {text}
"""
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

                        # Check for existing term in this workspace to detect conflicts
                        result = await db.execute(
                            select(DefinedTermRegistry).where(
                                DefinedTermRegistry.workspace_id == workspace_id,
                                DefinedTermRegistry.term == term_val
                            )
                        )
                        existing_terms = result.scalars().all()

                        is_conflict = False
                        conflict_desc = None
                        if existing_terms:
                            # A simple check: if definitions are drastically different
                            # In reality, an LLM would compare semantic similarity
                            is_conflict = True
                            conflict_desc = f"Conflict with definition in document {existing_terms[0].source_document_id}"

                        db.add(DefinedTermRegistry(
                            workspace_id=workspace_id,
                            org_id=org_id,
                            term=term_val,
                            definition=defn_val,
                            source_document_id=document_id,
                            page=page_num,
                            clause_reference=path_str,
                            conflict_flag=is_conflict,
                            conflict_description=conflict_desc
                        ))
                        terms_found += 1

                except Exception as e:
                    error_path = path_str or "Unknown Path"
                    print(f"  ⚠️ [terms_extractor] Extraction failed on [{error_path}]: {e}")
                    traceback.print_exc()

            await db.commit()

        print(f"[terms_extractor] ✓ Stored {terms_found} defined terms for document {document_id}.")
