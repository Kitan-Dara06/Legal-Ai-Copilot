"""
Defined Terms Extractor — PostgreSQL
======================================
LLM-assisted extraction of legal defined terms.
Stores results in defined_terms_registry via async SQLAlchemy.
"""

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional

from openai import OpenAI
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import DefinedTermRegistry

logger = logging.getLogger(__name__)

# ── LLM prompt template ──────────────────────────────────────────────────────

EXTRACTION_PROMPT_TEMPLATE = """You are a legal document analyst. Extract all **defined terms** from the following legal text.

A defined term is a word or phrase that is explicitly defined in the text — typically capitalised and enclosed in double quotes like "Confidential Information" or "Agreement".

Return a JSON object with a single key "terms" whose value is a list of objects, each containing:
  - "term": the defined term itself (e.g. "Confidential Information")
  - "definition": the text that defines it (e.g. "shall mean all non-public information disclosed by one party to the other")

If no defined terms are found, return {{"terms": []}}.

Text:
{text}
"""


class DefinedTermExtractor:
    """Extracts defined terms from legal text chunks using an LLM,
    then stores them in PostgreSQL via the DefinedTermRegistry model."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        workspace_id: Optional[str] = None,
        org_id: Optional[str] = None,
        source_document_id: Optional[str] = None,
    ):
        # db_path kept for backward-compat — ignored (uses DATABASE_URL via AsyncSessionLocal)
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get(
                "OPENAI_API_BASE", "https://api.groq.com/openai/v1"
            ),
        )
        self.model = os.environ.get("OPENAI_MODEL", "llama-3.1-8b-instant")
        self.workspace_id = workspace_id
        self.org_id = org_id
        self.source_document_id = source_document_id

    # ── Public API ──────────────────────────────────────────────────────────

    async def extract_and_store(
        self,
        chunks: List[Dict],
        document_name: str,
    ) -> int:
        """Process each text chunk, extract defined terms via LLM, and upsert
        them into the database.

        Parameters
        ----------
        chunks : list[dict]
            Each dict must contain keys ``text``, ``page_number``,
            and optionally ``hierarchy``.
        document_name : str
            Human-readable document name (for logging).

        Returns
        -------
        int
            Number of newly inserted terms.
        """
        total_new = 0

        for idx, chunk in enumerate(chunks):
            text = chunk.get("text", "")
            if not text.strip():
                continue

            page = chunk.get("page_number", 0)
            hierarchy = chunk.get("hierarchy", "")

            try:
                terms = await self._extract_terms(text)
            except Exception:
                logger.warning(
                    "LLM extraction failed for chunk %d of '%s': %s",
                    idx,
                    document_name,
                    traceback.format_exc(),
                )
                continue

            if not terms:
                logger.debug(
                    "Chunk %d of '%s' — no defined terms found.", idx, document_name
                )
                continue

            inserted = await self._store_terms(
                terms=terms,
                page=page,
                clause_reference=hierarchy,
                source_document_id=self.source_document_id,
            )
            total_new += inserted

        logger.info(
            "Defined term extraction complete for '%s': %d new terms stored.",
            document_name,
            total_new,
        )
        return total_new

    # ── LLM call ───────────────────────────────────────────────────────────

    async def _extract_terms(self, text: str) -> List[Dict]:
        """Send a single chunk to the LLM and return parsed terms list."""
        prompt = EXTRACTION_PROMPT_TEMPLATE.format(text=text[:8000])

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You extract defined terms from legal documents. "
                    "Always respond in valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        raw = response.choices[0].message.content
        if not raw:
            return []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON: %.200s", raw)
            return []

        return payload.get("terms", [])

    # ── Database operations ────────────────────────────────────────────────

    async def _store_terms(
        self,
        terms: List[Dict],
        page: int,
        clause_reference: str,
        source_document_id: Optional[str],
    ) -> int:
        """Insert terms that don't already exist for this source document.

        Returns the number of newly inserted rows.
        """
        if not terms:
            return 0

        inserted = 0

        async with AsyncSessionLocal() as db:
            for entry in terms:
                term_text = (entry.get("term") or "").strip()
                definition_text = (entry.get("definition") or "").strip()

                if not term_text or not definition_text:
                    continue

                # ── duplicate check ───────────────────────────────────────
                stmt = select(DefinedTermRegistry).where(
                    DefinedTermRegistry.term == term_text,
                    DefinedTermRegistry.source_document_id == source_document_id,
                )
                existing = (await db.execute(stmt)).scalar_one_or_none()

                if existing is not None:
                    logger.debug("Term '%s' already exists — skipping.", term_text)
                    continue

                # ── insert ────────────────────────────────────────────────
                record = DefinedTermRegistry(
                    workspace_id=self.workspace_id,
                    org_id=self.org_id,
                    term=term_text,
                    definition=definition_text,
                    source_document_id=source_document_id,
                    page=page,
                    clause_reference=clause_reference,
                    conflict_flag=False,
                    conflict_description=None,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(record)
                inserted += 1

            await db.commit()

        return inserted
