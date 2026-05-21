"""
Legal Document Parser
=====================
Supports PDF (via PyMuPDF) and DOCX (via python-docx).

Both parsers return a list of dicts:
    {
        "text":        str,
        "is_bold":     bool,
        "font_size":   float,
        "page_number": int,   # 1-indexed; DOCX uses paragraph index as a proxy
    }

The font calibration baseline is derived from real document statistics for PDF.
DOCX uses Heading style names to infer is_bold/font_size equivalents.

All parse operations are logged to MongoDB audit.
"""

import re
import time
from collections import Counter
from typing import Dict, List

import fitz  # PyMuPDF


class LegalDocumentParser:
    def __init__(self):
        self.body_font_size = 12.0

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------

    def _calibrate_font_baseline(
        self, doc: fitz.Document, sample_pages: int = 5
    ) -> float:
        font_sizes = []
        pages_to_scan = min(sample_pages, len(doc))

        for page_num in range(pages_to_scan):
            page = doc[page_num]
            blocks = page.get_text("dict").get("blocks", [])

            for block in blocks:
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if len(text) > 5:
                            size = round(span["size"], 1)
                            font_sizes.append(size)

        if not font_sizes:
            return 12.0

        size_counts = Counter(font_sizes)
        most_common_size = size_counts.most_common(1)[0][0]
        return most_common_size

    def parse_pdf(self, filepath: str) -> List[Dict]:
        """Extract blocks from a PDF with font calibration. Logged to MongoDB audit."""
        _t0 = time.time()
        doc = fitz.open(filepath)
        self.body_font_size = self._calibrate_font_baseline(doc)

        extracted_blocks = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict").get("blocks", [])

            for block in blocks:
                if "lines" not in block:
                    continue

                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text:
                            continue

                        extracted_blocks.append(
                            {
                                "text": text,
                                "is_bold": bool(span["flags"] & 2),
                                "font_size": span["size"],
                                "page_number": page_num + 1,
                            }
                        )

        doc.close()
        _elapsed = (time.time() - _t0) * 1000
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import DebugCategory, DebugTrace

            AuditLogger.debug(
                DebugTrace(
                    category=DebugCategory.NODE_EXIT,
                    message=f"parse_pdf: {len(extracted_blocks)} blocks ({_elapsed:.0f}ms)",
                    duration_ms=_elapsed,
                    metadata={
                        "filepath": filepath[-60:],
                        "blocks": len(extracted_blocks),
                        "pages": len(doc) if hasattr(doc, "__len__") else 0,
                    },
                )
            )
        except Exception:
            pass
        return extracted_blocks

    # ------------------------------------------------------------------
    # DOCX
    # ------------------------------------------------------------------

    _DOCX_HEADING_FONT_MAP: Dict[str, float] = {
        "heading 1": 16.0,
        "heading 2": 14.0,
        "heading 3": 13.0,
        "heading 4": 12.5,
    }

    def parse_docx(self, filepath: str) -> List[Dict]:
        """
        Extract blocks from a DOCX using python-docx.
        Logged to MongoDB audit.

        Paragraph style names are mapped to is_bold and font_size
        equivalents so the ClauseChunker receives the same dict shape
        it gets from parse_pdf().
        """
        from docx import Document

        _t0 = time.time()
        doc = Document(filepath)
        extracted_blocks = []

        run_sizes = []
        for para in doc.paragraphs:
            style_name = (para.style.name or "").lower()
            if style_name not in self._DOCX_HEADING_FONT_MAP:
                for run in para.runs:
                    if run.font.size:
                        run_sizes.append(round(run.font.size.pt, 1))

        if run_sizes:
            size_counts = Counter(run_sizes)
            self.body_font_size = size_counts.most_common(1)[0][0]
        else:
            self.body_font_size = 12.0

        paragraphs_per_page = 30

        for idx, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not text:
                continue

            style_name = (para.style.name or "Normal").lower()
            page_number = (idx // paragraphs_per_page) + 1

            if style_name in self._DOCX_HEADING_FONT_MAP:
                is_bold = True
                font_size = self._DOCX_HEADING_FONT_MAP[style_name]
            else:
                is_bold = any(run.bold for run in para.runs if run.bold is not None)
                sizes = [
                    run.font.size.pt for run in para.runs if run.font.size is not None
                ]
                font_size = max(sizes) if sizes else self.body_font_size

            extracted_blocks.append(
                {
                    "text": text,
                    "is_bold": is_bold,
                    "font_size": font_size,
                    "page_number": page_number,
                }
            )

        _elapsed = (time.time() - _t0) * 1000
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import DebugCategory, DebugTrace

            AuditLogger.debug(
                DebugTrace(
                    category=DebugCategory.NODE_EXIT,
                    message=f"parse_docx: {len(extracted_blocks)} blocks ({_elapsed:.0f}ms)",
                    duration_ms=_elapsed,
                    metadata={
                        "filepath": filepath[-60:],
                        "blocks": len(extracted_blocks),
                    },
                )
            )
        except Exception:
            pass
        return extracted_blocks

    # ------------------------------------------------------------------
    # Markdown (For OCR output)
    # ------------------------------------------------------------------

    def parse_markdown(self, pages_data: List[Dict]) -> List[Dict]:
        """
        Converts Markdown output from Gemini OCR into standard blocks.
        Logged to MongoDB audit.
        """
        _t0 = time.time()
        extracted_blocks = []
        for page in pages_data:
            page_num = page.get("page", 1)
            text = page.get("text", "")

            lines = text.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if line.startswith("#"):
                    level = len(line) - len(line.lstrip("#"))
                    clean_text = line.lstrip("#").strip()

                    if level == 1:
                        font_size = 18.0
                    elif level == 2:
                        font_size = 16.0
                    elif level == 3:
                        font_size = 14.0
                    else:
                        font_size = 13.0

                    extracted_blocks.append(
                        {
                            "text": clean_text,
                            "is_bold": True,
                            "font_size": font_size,
                            "page_number": page_num,
                        }
                    )
                else:
                    extracted_blocks.append(
                        {
                            "text": line,
                            "is_bold": False,
                            "font_size": 12.0,
                            "page_number": page_num,
                        }
                    )

        _elapsed = (time.time() - _t0) * 1000
        try:
            from app.services.audit.logger import AuditLogger
            from app.services.audit.schemas import DebugCategory, DebugTrace

            AuditLogger.debug(
                DebugTrace(
                    category=DebugCategory.NODE_EXIT,
                    message=f"parse_markdown: {len(extracted_blocks)} blocks ({_elapsed:.0f}ms)",
                    duration_ms=_elapsed,
                    metadata={
                        "blocks": len(extracted_blocks),
                        "pages": len(pages_data),
                    },
                )
            )
        except Exception:
            pass
        return extracted_blocks
