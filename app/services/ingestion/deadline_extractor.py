"""
Deadline & Obligation Extractor
===============================
Extracts obligations, deadlines, and required actions from legal chunks.
Stores results in `deadline_registry` via async SQLAlchemy.

SRS Stage 6 features:
- Concurrent chunk processing with Redis semaphore (5 LLM calls / org)
- Execution date extraction for relative date resolution
- Intra-document conflict detection (CONFLICTED status, source_clause_a/b)
- Cross-document conflict detection deferred to workspace sweep task
"""

import asyncio
import json
import os
import re
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from app.database import AsyncSessionLocal
from app.models import (
    DeadlineRegistry,
    DeadlineResolutionStatus,
    DeadlineStatus,
    ObligationType,
)
from app.redis_client import acquire_llm_slot, release_llm_slot

# ─────────────────────────────────────────────────────────────────────────────
# Relative date resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

_RELATIVE_PATTERNS = [
    # "within N days of/from/after <event>"
    (re.compile(r"within\s+(\d+)\s+days?\s+(?:of|from|after)\s+", re.I), "days"),
    # "within N business days of/from/after <event>"
    (
        re.compile(r"within\s+(\d+)\s+business\s+days?\s+(?:of|from|after)\s+", re.I),
        "business_days",
    ),
    # "N days after/from <event>"
    (re.compile(r"(\d+)\s+days?\s+(?:after|from)\s+", re.I), "days"),
    # "N business days after/from <event>"
    (
        re.compile(r"(\d+)\s+business\s+days?\s+(?:after|from)\s+", re.I),
        "business_days",
    ),
    # "N months after/from <event>"
    (re.compile(r"(\d+)\s+months?\s+(?:after|from)\s+", re.I), "months"),
    # "N weeks after/from <event>"
    (re.compile(r"(\d+)\s+weeks?\s+(?:after|from)\s+", re.I), "weeks"),
    # "on or before <date>"
    (re.compile(r"(?:on\s+or\s+)?before\s+", re.I), None),
    # "no later than <date>"
    (re.compile(r"no\s+later\s+than\s+", re.I), None),
    # "immediately" / "promptly"
    (re.compile(r"^(immediately|promptly|forthwith)$", re.I), "immediate"),
]

_DATE_PARSERS = [
    # ISO 8601
    (
        re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
        lambda m: (int(m[1]), int(m[2]), int(m[3])),
    ),
    # "Month DD, YYYY" or "DD Month YYYY"
    (
        re.compile(
            r"((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})",
            re.I,
        ),
        lambda m: _parse_month_name_date(m[1]),
    ),
    # MM/DD/YYYY or DD/MM/YYYY
    (
        re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"),
        lambda m: (int(m[2]), int(m[1]), int(m[3])),
    ),
]


def _parse_month_name_date(text: str) -> Optional[Tuple[int, int, int]]:
    """Parse 'January 15, 2024' or '15 Jan 2024' -> (year, month, day)."""
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    parts = text.replace(",", "").split()
    if len(parts) == 3:
        month_name = parts[0].lower()
        month = months.get(month_name)
        if month:
            if parts[1].isdigit():
                return (int(parts[2]), month, int(parts[1]))
            if parts[2].isdigit():
                return (int(parts[1]), month, int(parts[2]))
    return None


def _find_absolute_date(text: str) -> Optional[datetime]:
    """Scan text for a concrete date string and return it as UTC datetime."""
    for pattern, parser in _DATE_PARSERS:
        m = pattern.search(text)
        if m:
            result = parser(m)
            if result:
                year, month, day = result
                return datetime(year, month, day, tzinfo=timezone.utc)
    return None


def _resolve_relative_date(
    raw_date_expr: str,
    execution_date: datetime,
) -> Optional[datetime]:
    """
    Attempt to resolve a relative date expression against the execution date.
    Returns the resolved absolute datetime, or None if it cannot be resolved.
    """
    expr_lower = raw_date_expr.strip().lower()

    # "immediately" -> execution date
    if expr_lower in ("immediately", "promptly", "forthwith"):
        return execution_date

    for pattern, unit in _RELATIVE_PATTERNS:
        m = pattern.search(expr_lower)
        if m:
            if unit == "immediate":
                return execution_date
            if unit in ("days", "weeks", "months"):
                count = int(m.group(1))
                if unit == "days":
                    return execution_date + timedelta(days=count)
                elif unit == "weeks":
                    return execution_date + timedelta(weeks=count)
                elif unit == "months":
                    # Approximate: add months by adjusting the month number
                    total_months = execution_date.month + count
                    new_year = execution_date.year + (total_months - 1) // 12
                    new_month = ((total_months - 1) % 12) + 1
                    return datetime(
                        new_year,
                        new_month,
                        execution_date.day,
                        tzinfo=timezone.utc,
                    )
            elif unit == "business_days":
                count = int(m.group(1))
                current = execution_date
                added = 0
                while added < count:
                    current += timedelta(days=1)
                    if current.weekday() < 5:  # Mon-Fri
                        added += 1
                return current
            break

    return None


def _obligations_conflict(desc_a: str, date_a: str, desc_b: str, date_b: str) -> bool:
    """
    Rough conflict check: same obligation type + similar description
    + different raw dates = conflict.
    Uses a simple normalized string comparison for descriptions.
    """
    if date_a == date_b:
        return False
    # Normalize descriptions: lowercase, strip whitespace, take first 80 chars
    norm_a = desc_a.lower().strip()[:80]
    norm_b = desc_b.lower().strip()[:80]
    if not norm_a or not norm_b:
        return False
    # If descriptions are very different, it's probably a different obligation
    # Use a simple containment / overlap heuristic
    words_a = set(norm_a.split())
    words_b = set(norm_b.split())
    if len(words_a) < 3 or len(words_b) < 3:
        return norm_a == norm_b
    overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
    return overlap >= 0.4


# ─────────────────────────────────────────────────────────────────────────────
# Deadline Extractor
# ─────────────────────────────────────────────────────────────────────────────


class DeadlineExtractor:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ).rstrip("/"),
        )
        self.model = "meta-llama/llama-3.1-8b-instruct"

    async def _extract_execution_date(
        self, chunks: List[Dict], org_id: uuid.UUID
    ) -> Optional[datetime]:
        """
        Scan the document for an execution/effective/signing date.
        Checks the first few chunks plus any chunk containing "execution date",
        "effective date", or "signing date" keywords.
        """
        # Collect candidate chunks: first 3 + any with date keywords
        date_keywords = [
            "execution date",
            "effective date",
            "signing date",
            "dated as of",
            "entered into",
            "agreement date",
        ]
        candidates = []
        for i, chunk in enumerate(chunks):
            text_lower = (chunk.get("text", "") or "").lower()
            if i < 3:
                candidates.append(chunk)
            elif any(kw in text_lower for kw in date_keywords):
                candidates.append(chunk)
            if len(candidates) >= 5:
                break

        if not candidates:
            return None

        # First try: look for a concrete date string in candidates
        for chunk in candidates:
            text = chunk.get("text", "") or ""
            found = _find_absolute_date(text)
            if found:
                return found

        # Second try: ask LLM to extract it
        combined_text = "\n\n---\n\n".join(c.get("text", "")[:2000] for c in candidates)

        prompt = f"""Extract the execution date, effective date, or signing date of this legal document.
Return ONLY a valid JSON object with the field "date" containing the date string you found,
or null if no date is mentioned.

Examples:
{{"date": "January 15, 2024"}}
{{"date": "2024-01-15"}}
{{"date": null}}

Text:
{combined_text[:6000]}
"""
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
            raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
            parsed = json.loads(raw)
            date_str = parsed.get("date")
            if date_str:
                # Try to parse the returned date string
                parsed_date = _find_absolute_date(date_str)
                if parsed_date:
                    return parsed_date
        except Exception as e:
            print(f"  [deadline_extractor] Execution date extraction failed: {e}")
        finally:
            if lease_id:
                await release_llm_slot(str(org_id), lease_id)

        return None

    async def extract_and_store(
        self,
        chunks: List[Dict],
        document_id: uuid.UUID,
        workspace_id: uuid.UUID,
        org_id: uuid.UUID,
    ):
        """Processes chunks concurrently, extracts deadlines, resolves relative dates, detects intra-doc conflicts."""
        print(
            f"[deadline_extractor] Extracting deadlines from document {document_id}..."
        )

        # ── Step 0: Extract execution date for relative date resolution ──
        execution_date = await self._extract_execution_date(chunks, org_id)
        if execution_date:
            print(
                f"  [deadline_extractor] Found execution date: {execution_date.date()}"
            )
        else:
            print(
                "  [deadline_extractor] No execution date found; dates will remain RELATIVE_UNRESOLVED"
            )

        # ── Step 1: Concurrent LLM extraction ──
        # Collect all extracted obligations for post-processing
        collected_obligations: List[dict] = []
        conflict_pairs: List[tuple] = []

        async def process_chunk(chunk, db):
            text = chunk["text"]
            if len(text) < 50:
                return

            path_str = " > ".join(chunk.get("hierarchy", []))

            # Pre-filter: only run LLM if chunk contains temporal keywords
            temporal_keywords = [
                "shall",
                "must",
                "days",
                "within",
                "prior to",
                "deadline",
                "date",
                "notice",
                "payment",
            ]
            if not any(kw in text.lower() for kw in temporal_keywords):
                return

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

                    # Try to resolve the relative date
                    resolved = None
                    resolution_status = DeadlineResolutionStatus.RELATIVE_UNRESOLVED

                    if execution_date:
                        # First check if the raw expression contains an absolute date
                        abs_date = _find_absolute_date(raw_date)
                        if abs_date:
                            resolved = abs_date
                            resolution_status = DeadlineResolutionStatus.RESOLVED
                        else:
                            # Try relative resolution
                            resolved = _resolve_relative_date(raw_date, execution_date)
                            if resolved:
                                resolution_status = DeadlineResolutionStatus.RESOLVED

                    obligation_record = {
                        "workspace_id": workspace_id,
                        "org_id": org_id,
                        "obligation_description": desc,
                        "obligation_type": obl_type,
                        "raw_date_expression": raw_date,
                        "resolved_deadline": resolved,
                        "resolution_status": resolution_status,
                        "conflict_flag": False,
                        "source_clause_a": {
                            "document_id": str(document_id),
                            "hierarchy": chunk.get("hierarchy", []),
                            "text": text,
                        },
                        "source_clause_b": None,
                        "status": DeadlineStatus.ACTIVE,
                        "urgency_score": 0.5,
                    }

                    # Store the pending DB object and also collect for conflict scan
                    registry_entry = DeadlineRegistry(**obligation_record)
                    db.add(registry_entry)

                    collected_obligations.append(
                        {
                            "registry_entry": registry_entry,
                            "description": desc,
                            "raw_date_expression": raw_date,
                            "obl_type": obl_type,
                            "source_clause_a": obligation_record["source_clause_a"],
                        }
                    )

            except Exception as e:
                error_path = path_str or "Unknown Path"
                print(
                    f"  \u26a0\ufe0f [deadline_extractor] Extraction failed on [{error_path}]: {e}"
                )
                traceback.print_exc()
            finally:
                if lease_id:
                    await release_llm_slot(str(org_id), lease_id)

        async with AsyncSessionLocal() as db:
            await asyncio.gather(*(process_chunk(chunk, db) for chunk in chunks))

            # ── Step 2: Intra-document conflict detection ──
            # Scan collected obligations for same type + similar description + different dates
            for i in range(len(collected_obligations)):
                for j in range(i + 1, len(collected_obligations)):
                    a = collected_obligations[i]
                    b = collected_obligations[j]
                    if a["obl_type"] != b["obl_type"]:
                        continue
                    if not _obligations_conflict(
                        a["description"],
                        a["raw_date_expression"],
                        b["description"],
                        b["raw_date_expression"],
                    ):
                        continue

                    # Found a conflict within this document
                    conflict_pairs.append((a, b))

            if conflict_pairs:
                print(
                    f"  [deadline_extractor] Found {len(conflict_pairs)} intra-document conflict(s)"
                )

            for a, b in conflict_pairs:
                # Mark both as conflicted
                a_entry = a["registry_entry"]
                b_entry = b["registry_entry"]
                a_entry.conflict_flag = True
                a_entry.resolution_status = DeadlineResolutionStatus.CONFLICTED
                a_entry.source_clause_b = b["source_clause_a"]

                b_entry.conflict_flag = True
                b_entry.resolution_status = DeadlineResolutionStatus.CONFLICTED
                b_entry.source_clause_b = a["source_clause_a"]

            await db.commit()

        print(
            f"[deadline_extractor] \u2713 Stored {len(collected_obligations)} deadlines "
            f"({len(conflict_pairs)} intra-doc conflicts) for document {document_id}."
        )
