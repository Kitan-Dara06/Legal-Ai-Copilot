# tests/unit/test_parser.py
"""
Unit tests for app.services.parser
"""
import io
import pytest
from app.services.parser import extract_from_pdf


def test_extract_from_pdf_invalid():
    """Should return empty list or raise cleanly on invalid bytes"""
    fake_bytes = io.BytesIO(b"not a real pdf")
    result = extract_from_pdf(fake_bytes)
    assert isinstance(result, list)
