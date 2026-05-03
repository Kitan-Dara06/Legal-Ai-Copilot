"""
Defined Terms Extractor — PostgreSQL
======================================
LLM-assisted extraction of legal defined terms.
Stores results in legaltech.defined_terms via SQLAlchemy (replaces sqlite3).
"""

import json
import os
import re
import traceback
from typing import Dict, List

from openai import OpenAI

from due_diligence.db.postgres import SessionLocal
from due_diligence.db.pg_models import DefinedTerm


class DefinedTermExtractor:
    def __init__(self, db_path: str = None):
        # db_path kept for backward-compat — ignored (uses DATABASE_URL)
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("OPENAI_API_BASE", "https://api.groq.com/openai/v1"),
        )
        self.model = "llama-3.1-8b-instant"

    def extract_and_store(self, chunks: List[Dict], document_name: str):
        """Processes chunks, extracts definitions, upserts to PostgreSQL."""
        print(f"Extracting defined terms from '{document_name}'...")

        terms_found = 0
        with SessionLocal() as db:
            for chunk in chunks:
                text = chunk["text"]
                path_str = " > ".join(chunk.get("hierarchy", []))

                prompt = f"""Identify any explicitly "Defined Terms" in the following text.
These are usually capitalized and enclosed in quotes (e.g., "Confidential Information", "Seller").

You MUST output valid JSON in this exact structure:
{{"terms": [{{"term": "...", "definition": "..."}}]}}

If there are no defined terms, return: {{"terms": []}}

Text: {text}
"""
                try:
                    response = self.client.chat.completions.create(
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
                        # ON CONFLICT DO NOTHING via merge pattern
                        existing = (
                            due_diligence.db.query(DefinedTerm)
                            .filter_by(
                                term=term_val,
                                source_document=document_name,
                                hierarchy=path_str,
                            )
                            .first()
                        )
                        if not existing:
                            due_diligence.db.add(DefinedTerm(
                                term=term_val,
                                definition=defn_val,
                                source_document=document_name,
                                hierarchy=path_str,
                            ))
                            terms_found += 1

                except Exception as e:
                    error_path = path_str or "Unknown Path"
                    print(f"⚠️ Extraction failed on [{error_path}]: {e}")
                    traceback.print_exc()

            due_diligence.db.commit()

        print(f"✓ Stored {terms_found} defined terms for '{document_name}'.")
