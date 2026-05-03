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
"""

import re
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
        """Extract blocks from a PDF with font calibration."""
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
                                "page_number": page_num + 1,  # 1-indexed
                            }
                        )

        doc.close()
        return extracted_blocks

    # ------------------------------------------------------------------
    # DOCX
    # ------------------------------------------------------------------

    # Heading style names → approximate font_size equivalents for the chunker
    _DOCX_HEADING_FONT_MAP: Dict[str, float] = {
        "heading 1": 16.0,
        "heading 2": 14.0,
        "heading 3": 13.0,
        "heading 4": 12.5,
    }

    def parse_docx(self, filepath: str) -> List[Dict]:
        """
        Extract blocks from a DOCX using python-docx.

        Paragraph style names are mapped to is_bold and font_size
        equivalents so the ClauseChunker receives the same dict shape
        it gets from parse_pdf().

        page_number is approximated: paragraph index divided by an
        assumed ~30 paragraphs-per-page, plus 1 (1-indexed).
        """
        from docx import Document  # local import — avoids hard dep when using PDF only

        doc = Document(filepath)
        extracted_blocks = []

        # Use the modal run font size as body_font_size baseline (same logic as PDF)
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

        paragraphs_per_page = 30  # conservative proxy for page estimation

        for idx, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not text:
                continue

            style_name = (para.style.name or "Normal").lower()
            page_number = (idx // paragraphs_per_page) + 1

            # Determine is_bold and font_size from style name
            if style_name in self._DOCX_HEADING_FONT_MAP:
                is_bold = True
                font_size = self._DOCX_HEADING_FONT_MAP[style_name]
            else:
                # Fall back to run-level inspection
                is_bold = any(run.bold for run in para.runs if run.bold is not None)
                # Use the largest run size, or the body baseline
                sizes = [
                    run.font.size.pt
                    for run in para.runs
                    if run.font.size is not None
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

        return extracted_blocks

    # ------------------------------------------------------------------
    # Markdown (For OCR output)
    # ------------------------------------------------------------------
    
    def parse_markdown(self, pages_data: List[Dict]) -> List[Dict]:
        """
        Converts Markdown output from Gemini OCR into standard blocks.
        Expects pages_data: [{"page": 1, "text": "## Article 1\n..."}]
        """
        extracted_blocks = []
        for page in pages_data:
            page_num = page.get("page", 1)
            text = page.get("text", "")
            
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # Check for markdown headers
                if line.startswith('#'):
                    # Count number of hashes
                    level = len(line) - len(line.lstrip('#'))
                    clean_text = line.lstrip('#').strip()
                    
                    # Map header level to font size
                    if level == 1:
                        font_size = 18.0
                    elif level == 2:
                        font_size = 16.0
                    elif level == 3:
                        font_size = 14.0
                    else:
                        font_size = 13.0
                        
                    extracted_blocks.append({
                        "text": clean_text,
                        "is_bold": True,
                        "font_size": font_size,
                        "page_number": page_num
                    })
                else:
                    # Regular text
                    extracted_blocks.append({
                        "text": line,
                        "is_bold": False,
                        "font_size": self.body_font_size,
                        "page_number": page_num
                    })
                    
        return extracted_blocks


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python parser.py <path_to_pdf_or_docx>")
        sys.exit(1)

    target = sys.argv[1]
    parser = LegalDocumentParser()

    if target.lower().endswith(".pdf"):
        blocks = parser.parse_pdf(target)
    elif target.lower().endswith(".docx"):
        blocks = parser.parse_docx(target)
    else:
        print("Unsupported file type")
        sys.exit(1)

    print(f"Extracted {len(blocks)} text blocks. Body font baseline: {parser.body_font_size}pt")
    for b in blocks[:5]:
        print(f"  [p{b['page_number']} | bold={b['is_bold']} | {b['font_size']}pt] {b['text'][:80]}")
