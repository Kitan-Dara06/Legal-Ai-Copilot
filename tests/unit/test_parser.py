# tests/unit/test_parser.py
"""
Unit tests for app.services.ingestion.parser.LegalDocumentParser
"""
import pytest
from app.services.ingestion.parser import LegalDocumentParser


def test_parse_markdown():
    """Should correctly parse simple markdown headers and paragraphs into structured blocks"""
    parser = LegalDocumentParser()
    pages_data = [
        {
            "page": 1,
            "text": "# ARTICLE 1\nThis is paragraph text.\n## 1.1 Section"
        }
    ]
    
    blocks = parser.parse_markdown(pages_data)
    
    assert len(blocks) == 3
    
    # Assert ARTICLE 1 is bold and size 18.0
    assert blocks[0]["text"] == "ARTICLE 1"
    assert blocks[0]["is_bold"] is True
    assert blocks[0]["font_size"] == 18.0
    assert blocks[0]["page_number"] == 1
    
    # Assert normal text is not bold and size 12.0
    assert blocks[1]["text"] == "This is paragraph text."
    assert blocks[1]["is_bold"] is False
    assert blocks[1]["font_size"] == 12.0
    assert blocks[1]["page_number"] == 1
    
    # Assert Section 1.1 is bold and size 16.0
    assert blocks[2]["text"] == "1.1 Section"
    assert blocks[2]["is_bold"] is True
    assert blocks[2]["font_size"] == 16.0
    assert blocks[2]["page_number"] == 1
