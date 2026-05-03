"""
Deadline & Obligation Extractor
===============================
Extracts obligations, deadlines, and required actions from legal chunks.
Stores results in `deadline_registry` via async SQLAlchemy.
"""

import json
import os
import re
import uuid
import traceback
from typing import Dict, List

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeadlineRegistry, ObligationType, DeadlineResolutionStatus, DeadlineStatus
from app.database import AsyncSessionLocal

class DeadlineExtractor:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
        )
        self.model = "meta-llama/llama-3.1-8b-instruct"

    async def extract_and_store(
        self, 
        chunks: List[Dict], 
        document_id: uuid.UUID, 
        workspace_id: uuid.UUID, 
        org_id: uuid.UUID
    ):
        """Processes chunks, extracts deadlines/obligations, upserts to PostgreSQL."""
        print(f"[deadline_extractor] Extracting deadlines from document {document_id}...")

        deadlines_found = 0
        async with AsyncSessionLocal() as db:
            for chunk in chunks:
                text = chunk["text"]
                if len(text) < 50:
                    continue
                    
                path_str = " > ".join(chunk.get("hierarchy", []))
                
                # Pre-filter: only run LLM if chunk contains temporal keywords
                temporal_keywords = ["shall", "must", "days", "within", "prior to", "deadline", "date", "notice", "payment"]
                if not any(kw in text.lower() for kw in temporal_keywords):
                    continue

                prompt = f"""Identify any strict legal obligations or deadlines in the following text.
Categorize the obligation_type as one of: RESPONSE, FILING, NOTICE, PAYMENT, or OTHER.

You MUST output valid JSON in this exact structure:
{{
  "obligations": [
    {{
      "description": "...", 
      "type": "NOTICE", 
      "raw_date_expression": "within 30 days of termination"
    }}
  ]
}}

If there are no obligations, return: {{"obligations": []}}

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
                    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw)

                    for item in parsed.get("obligations", []):
                        desc = item.get("description", "").strip()
                        raw_date = item.get("raw_date_expression", "").strip()
                        obl_type_str = item.get("type", "OTHER").upper()
                        
                        if not desc or not raw_date:
                            continue
                            
                        try:
                            obl_type = ObligationType(obl_type_str)
                        except ValueError:
                            obl_type = ObligationType.OTHER

                        # For now, store as RELATIVE_UNRESOLVED since we need a base date to resolve it
                        db.add(DeadlineRegistry(
                            workspace_id=workspace_id,
                            org_id=org_id,
                            obligation_description=desc,
                            obligation_type=obl_type,
                            raw_date_expression=raw_date,
                            resolution_status=DeadlineResolutionStatus.RELATIVE_UNRESOLVED,
                            conflict_flag=False,
                            source_clause_a={
                                "document_id": str(document_id),
                                "hierarchy": chunk.get("hierarchy", []),
                                "text": text
                            },
                            status=DeadlineStatus.ACTIVE,
                            urgency_score=0.5
                        ))
                        deadlines_found += 1

                except Exception as e:
                    error_path = path_str or "Unknown Path"
                    print(f"  ⚠️ [deadline_extractor] Extraction failed on [{error_path}]: {e}")
                    traceback.print_exc()

            await db.commit()

        print(f"[deadline_extractor] ✓ Stored {deadlines_found} deadlines for document {document_id}.")
