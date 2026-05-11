# tests/unit/test_chunker.py
"""
Unit tests for app.services.chunker
"""
import pytest
from app.services.chunker import chunk_text


def test_chunk_text_basic():
    pages = [{"page": 1, "text": "This is a sample legal contract clause. " * 20}]
    chunks = chunk_text(pages)
    assert len(chunks) > 0
    for chunk in chunks:
        assert "chunk_text" in chunk
        assert "page_number" in chunk


def test_chunk_text_empty():
    chunks = chunk_text([])
    assert chunks == []
