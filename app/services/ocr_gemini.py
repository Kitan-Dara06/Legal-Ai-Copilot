"""
Gemini Vision OCR for scanned PDFs → structured Markdown per page.

Design goals:
- No system OCR deps (no tesseract/poppler).
- Accept raw PDF bytes and return a list of {"page": int, "text": str} records.
- Produce Markdown that preserves legal document structure (headings, lists, tables).
- Be safe-by-default: do not hallucinate, do not invent missing text.

Requirements:
- Set env var GEMINI_API_KEY.
- Optional: GEMINI_MODEL (default: gemini-2.5-pro).

Notes:
- Google Gemini supports direct PDF inputs. This module uses the "google-genai" SDK.
- If the SDK is not installed, calling code will raise a RuntimeError with guidance.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional


class GeminiOcrError(RuntimeError):
    """Raised when Gemini OCR fails or is misconfigured."""


@dataclass(frozen=True)
class GeminiOcrConfig:
    api_key: str
    model: str = "gemini-2.5-pro"
    # Token budget is a safety rail; OCR markdown for large PDFs can be big.
    # If you hit truncation, lower page batch size or raise this.
    max_output_tokens: int = 8192
    temperature: float = 0.0
    # When True, wraps output in a strict JSON schema. We keep it False because
    # the rest of the pipeline expects plain markdown text.
    json_mode: bool = False


def _load_config(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> GeminiOcrConfig:
    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        raise GeminiOcrError(
            "GEMINI_API_KEY is missing. Set it in your environment / .env.production."
        )
    mdl = (model or os.getenv("GEMINI_MODEL", "")).strip() or "gemini-2.5-pro"
    return GeminiOcrConfig(api_key=key, model=mdl)


def _import_google_genai():
    """
    Import google-genai lazily so the rest of the app can run without it
    (e.g., in environments that don't process scanned PDFs).
    """
    try:
        # New SDK name (preferred)
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore

        return genai, types
    except Exception as e:
        # Raise a consistent, debuggable error
        raise GeminiOcrError(
            "Gemini OCR requires the 'google-genai' package. "
            "Install it (pip install google-genai) and rebuild your containers."
        ) from e


def _build_prompt(page_count_hint: int | None = None) -> str:
    """
    Prompt engineered for:
    - faithful transcription
    - structured Markdown
    - per-page segmentation
    """
    page_line = (
        f"The PDF contains {page_count_hint} page(s)."
        if page_count_hint
        else "The PDF contains multiple pages."
    )

    # Important: For scanned docs, we only want transcription + layout.
    # No summaries, no rewriting.
    return f"""You are an OCR engine for legal documents.

Task:
- Extract ALL visible text from the provided PDF.
- Output MUST be structured Markdown that preserves the document's hierarchy and layout.

{page_line}

Output rules (STRICT):
1) Do not summarize. Do not paraphrase. Do not add commentary.
2) Do not invent missing text. If something is unreadable, write: [illegible].
3) Preserve structure:
   - Use Markdown headings for clear section titles (e.g., "# ARTICLE 1", "## 2.1 Termination").
   - Preserve numbered clauses and bullet lists.
   - Preserve tables using Markdown pipe tables when possible.
4) Return PER-PAGE output in this exact format:

===PAGE 1===
(markdown for page 1)

===PAGE 2===
(markdown for page 2)

...continue for every page.

5) Keep citations, footers, headers, and page numbers if present, but do not duplicate them across pages.
"""


_PAGE_SPLIT_RE = re.compile(r"^===PAGE\s+(\d+)\s*===$", re.MULTILINE)


def _split_pages(markdown_blob: str, fallback_page_count: int = 1) -> List[Dict]:
    """
    Parses Gemini output into [{"page": int, "text": str}, ...].

    If the model fails to add page delimiters, we return a single page.
    """
    if not isinstance(markdown_blob, str):
        markdown_blob = str(markdown_blob or "")

    matches = list(_PAGE_SPLIT_RE.finditer(markdown_blob))
    if not matches:
        text = markdown_blob.strip()
        if not text:
            return [{"page": 1, "text": ""}]
        return [{"page": 1, "text": text}]

    pages: List[Dict] = []
    for i, m in enumerate(matches):
        page_num = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_blob)
        page_text = markdown_blob[start:end].strip()
        pages.append({"page": page_num, "text": page_text})

    # Ensure contiguous pages; if not, keep what we have (do not fabricate).
    # Downstream chunker can handle missing/empty pages.
    pages.sort(key=lambda x: x["page"])
    return pages


def ocr_pdf_to_markdown_pages(
    pdf_bytes: bytes,
    *,
    page_count_hint: int | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> List[Dict]:
    """
    Convert PDF bytes to structured Markdown per page using Gemini.

    Returns:
        List[{"page": int, "text": str}]
    """
    if not pdf_bytes or not isinstance(pdf_bytes, (bytes, bytearray)):
        raise GeminiOcrError("pdf_bytes must be non-empty bytes")

    cfg = _load_config(api_key=api_key, model=model)
    genai, types = _import_google_genai()

    client = genai.Client(api_key=cfg.api_key)

    prompt = _build_prompt(page_count_hint=page_count_hint)

    # google-genai accepts PDF bytes as a "Part" with mime_type application/pdf
    pdf_part = types.Part.from_bytes(data=bytes(pdf_bytes), mime_type="application/pdf")

    try:
        resp = client.models.generate_content(
            model=cfg.model,
            contents=[prompt, pdf_part],
            config=types.GenerateContentConfig(
                temperature=cfg.temperature,
                max_output_tokens=cfg.max_output_tokens,
            ),
        )
    except Exception as e:
        raise GeminiOcrError(f"Gemini OCR call failed: {type(e).__name__}: {e}") from e

    text = getattr(resp, "text", None)
    if not text or not str(text).strip():
        raise GeminiOcrError("Gemini OCR returned empty text output")

    pages = _split_pages(str(text), fallback_page_count=(page_count_hint or 1))

    # Strip empty whitespace-only pages; keep page numbers stable.
    normalized: List[Dict] = []
    for p in pages:
        md = (p.get("text") or "").strip()
        if md:
            normalized.append({"page": int(p.get("page") or 1), "text": md})

    return normalized


def ocr_pdf_path_to_markdown_pages(
    pdf_path: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> List[Dict]:
    """
    Convenience wrapper for reading from disk.
    """
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
    except Exception as e:
        raise GeminiOcrError(f"Could not read PDF path '{pdf_path}': {e}") from e

    return ocr_pdf_to_markdown_pages(data, api_key=api_key, model=model)
