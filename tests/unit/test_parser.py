"""
Unit tests for app.services.ingestion.parser.LegalDocumentParser.

Tests PDF parsing with PyMuPDF (fitz) fully mocked.
No real files, no I/O, no external dependencies.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.ingestion.parser import LegalDocumentParser

# ---------------------------------------------------------------------------
# Helpers to build mock fitz structures
# ---------------------------------------------------------------------------


def _make_mock_span(text: str, size: float = 12.0, flags: int = 0) -> dict:
    """Create a mock span dict as returned by page.get_text('dict')."""
    return {
        "text": text,
        "size": size,
        "flags": flags,  # bit 1 (value 2) = bold
        "font": "Times-Roman",
        "color": 0,
    }


def _make_mock_line(spans: list[dict]) -> dict:
    """Create a mock line dict containing spans."""
    return {
        "spans": spans,
        "wdir": (1.0, 0.0),
    }


def _make_mock_block(lines: list[dict]) -> dict:
    """Create a mock block dict containing lines."""
    return {
        "type": 0,  # text block
        "lines": lines,
        "bbox": (0, 0, 612, 792),
    }


def _make_mock_page(blocks: list[dict]) -> MagicMock:
    """Create a mock page whose get_text returns the given blocks."""
    page = MagicMock()
    page.get_text.return_value = {"blocks": blocks}
    return page


def _make_mock_document(pages: list[MagicMock], num_pages: int = None) -> MagicMock:
    """Create a mock fitz.Document that iterates over pages."""
    doc = MagicMock()
    doc.__len__.return_value = num_pages if num_pages is not None else len(pages)
    doc.__getitem__.side_effect = lambda idx: (
        pages[idx] if idx < len(pages) else MagicMock()
    )
    doc.iter.side_effect = lambda: iter(pages)
    return doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLegalDocumentParserCalibrateFontBaseline:
    """Tests for the _calibrate_font_baseline method."""

    @patch("app.services.ingestion.parser.fitz")
    def test_calibrate_picks_most_common_size(self, mock_fitz):
        """
        Given a document with spans of varying sizes, the calibration
        should return the most frequently occurring font size.
        """
        # Most common = 12.0 (3 spans), others = 14.0 (2 spans), 10.0 (1 span)
        spans_1 = [
            _make_mock_span("Short", size=12.0),
            _make_mock_span("Hello world this is text", size=12.0),
            _make_mock_span("Another longer span here", size=14.0),
        ]
        spans_2 = [
            _make_mock_span("Some text content here", size=12.0),
            _make_mock_span("Heading like text", size=14.0),
            _make_mock_span("Small footnote", size=10.0),
        ]
        page1 = _make_mock_page([_make_mock_block([_make_mock_line(spans_1)])])
        page2 = _make_mock_page([_make_mock_block([_make_mock_line(spans_2)])])

        mock_doc = _make_mock_document([page1, page2], num_pages=2)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        result = parser._calibrate_font_baseline(mock_doc, sample_pages=5)

        # 12.0 appears 3 times → most common
        assert result == 12.0
        mock_fitz.open.assert_not_called()  # doc was passed directly

    @patch("app.services.ingestion.parser.fitz")
    def test_calibrate_falls_back_to_default_when_no_text(self, mock_fitz):
        """
        When no spans with text longer than 5 characters are found,
        calibration should return the default 12.0.
        """
        # Only very short spans (< 6 chars) → should be ignored
        spans = [
            _make_mock_span("Hi", size=8.0),
            _make_mock_span("OK", size=10.0),
            _make_mock_span("Bye", size=14.0),
        ]
        page = _make_mock_page([_make_mock_block([_make_mock_line(spans)])])
        mock_doc = _make_mock_document([page], num_pages=1)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        result = parser._calibrate_font_baseline(mock_doc, sample_pages=5)

        assert result == 12.0

    @patch("app.services.ingestion.parser.fitz")
    def test_calibrate_empty_document_returns_default(self, mock_fitz):
        """
        An empty document (no pages) should return the default 12.0.
        """
        mock_doc = _make_mock_document([], num_pages=0)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        result = parser._calibrate_font_baseline(mock_doc, sample_pages=5)

        assert result == 12.0

    @patch("app.services.ingestion.parser.fitz")
    def test_calibrate_respects_sample_pages_limit(self, mock_fitz):
        """
        When sample_pages is smaller than the total page count, only
        the first sample_pages pages should be scanned.
        """
        spans = [_make_mock_span("Long enough text here", size=11.0)]
        pages = [
            _make_mock_page([_make_mock_block([_make_mock_line(spans)])])
            for _ in range(20)
        ]
        mock_doc = _make_mock_document(pages, num_pages=20)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        result = parser._calibrate_font_baseline(mock_doc, sample_pages=3)

        assert result == 11.0
        # Only pages 0, 1, 2 should be accessed
        assert mock_doc.__getitem__.call_count == 3


class TestLegalDocumentParserParsePDF:
    """Tests for the parse_pdf method."""

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_single_page_extracts_blocks(self, mock_fitz):
        """
        A single-page PDF with two text blocks should produce two
        extracted block dicts with the expected keys.
        """
        spans_1 = [_make_mock_span("Article 1: Definitions", size=14.0, flags=2)]
        spans_2 = [_make_mock_span("The term 'Company' means...", size=12.0)]
        block1 = _make_mock_block([_make_mock_line(spans_1)])
        block2 = _make_mock_block([_make_mock_line(spans_2)])

        page = _make_mock_page([block1, block2])
        mock_doc = _make_mock_document([page], num_pages=1)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        # Prevent calibration from interfering by stubbing it
        parser._calibrate_font_baseline = MagicMock(return_value=12.0)

        blocks = parser.parse_pdf("fake/path.pdf")

        assert len(blocks) == 2
        assert blocks[0]["text"] == "Article 1: Definitions"
        assert blocks[0]["is_bold"] is True
        assert blocks[0]["font_size"] == 14.0
        assert blocks[0]["page_number"] == 1

        assert blocks[1]["text"] == "The term 'Company' means..."
        assert blocks[1]["is_bold"] is False
        assert blocks[1]["font_size"] == 12.0
        assert blocks[1]["page_number"] == 1

        mock_fitz.open.assert_called_once_with("fake/path.pdf")
        mock_doc.close.assert_called_once()

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_empty_document_returns_empty_list(self, mock_fitz):
        """
        A PDF document with no pages should return an empty list.
        """
        mock_doc = _make_mock_document([], num_pages=0)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        parser._calibrate_font_baseline = MagicMock(return_value=12.0)

        blocks = parser.parse_pdf("empty.pdf")
        assert blocks == []

        mock_doc.close.assert_called_once()

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_multi_page_collects_all_blocks(self, mock_fitz):
        """
        A three-page PDF should collect blocks from all pages,
        with correct page_number metadata.
        """
        pages = []
        for pg in range(3):
            span = _make_mock_span(f"Text from page {pg + 1}", size=12.0)
            block = _make_mock_block([_make_mock_line([span])])
            pages.append(_make_mock_page([block]))

        mock_doc = _make_mock_document(pages, num_pages=3)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        parser._calibrate_font_baseline = MagicMock(return_value=12.0)

        blocks = parser.parse_pdf("multi.pdf")

        assert len(blocks) == 3
        assert blocks[0]["text"] == "Text from page 1"
        assert blocks[0]["page_number"] == 1
        assert blocks[1]["text"] == "Text from page 2"
        assert blocks[1]["page_number"] == 2
        assert blocks[2]["text"] == "Text from page 3"
        assert blocks[2]["page_number"] == 3

        mock_doc.close.assert_called_once()

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_skips_empty_text_spans(self, mock_fitz):
        """
        Spans with empty or whitespace-only text should be skipped,
        producing no block entries for them.
        """
        empty_span = _make_mock_span("   ", size=12.0)
        valid_span = _make_mock_span("Valid clause.", size=12.0)
        block = _make_mock_block([_make_mock_line([empty_span, valid_span])])

        page = _make_mock_page([block])
        mock_doc = _make_mock_document([page], num_pages=1)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        parser._calibrate_font_baseline = MagicMock(return_value=12.0)

        blocks = parser.parse_pdf("whitespace.pdf")

        assert len(blocks) == 1
        assert blocks[0]["text"] == "Valid clause."

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_calibrates_before_extraction(self, mock_fitz):
        """
        parse_pdf must call _calibrate_font_baseline before extracting
        blocks, so that self.body_font_size is set.
        """
        span = _make_mock_span("Content", size=12.0)
        block = _make_mock_block([_make_mock_line([span])])
        page = _make_mock_page([block])
        mock_doc = _make_mock_document([page], num_pages=1)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        # Spy on the calibration
        original_calibrate = parser._calibrate_font_baseline
        calibrate_called = False

        def tracking_calibrate(doc, sample_pages=5):
            nonlocal calibrate_called
            calibrate_called = True
            return original_calibrate(doc, sample_pages)

        parser._calibrate_font_baseline = tracking_calibrate

        parser.parse_pdf("fake.pdf")
        assert calibrate_called

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_handles_image_blocks_gracefully(self, mock_fitz):
        """
        Blocks that contain no lines (e.g. image blocks) should be
        safely skipped without error.
        """
        # Image block: has no 'lines' key
        image_block = {"type": 1, "bbox": (0, 0, 100, 100)}
        text_span = _make_mock_span("Text after image", size=12.0)
        text_block = _make_mock_block([_make_mock_line([text_span])])

        page = _make_mock_page([image_block, text_block])
        mock_doc = _make_mock_document([page], num_pages=1)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        parser._calibrate_font_baseline = MagicMock(return_value=12.0)

        blocks = parser.parse_pdf("with_image.pdf")

        # Only the text block should be extracted
        assert len(blocks) == 1
        assert blocks[0]["text"] == "Text after image"

    @patch("app.services.ingestion.parser.fitz")
    def test_parse_pdf_bold_flag_detected(self, mock_fitz):
        """
        The is_bold flag should be True when the span's flags field
        has bit 1 set (value 2).
        """
        bold_span = _make_mock_span("Bold Heading", size=14.0, flags=2)  # bit 1 set
        normal_span = _make_mock_span("Normal body", size=12.0, flags=0)

        block = _make_mock_block(
            [
                _make_mock_line([bold_span]),
                _make_mock_line([normal_span]),
            ]
        )
        page = _make_mock_page([block])
        mock_doc = _make_mock_document([page], num_pages=1)
        mock_fitz.open.return_value = mock_doc

        parser = LegalDocumentParser()
        parser._calibrate_font_baseline = MagicMock(return_value=12.0)

        blocks = parser.parse_pdf("bold_test.pdf")

        assert len(blocks) == 2
        assert blocks[0]["is_bold"] is True
        assert blocks[0]["text"] == "Bold Heading"
        assert blocks[1]["is_bold"] is False
        assert blocks[1]["text"] == "Normal body"
