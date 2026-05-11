"""
Unit tests for the Citation Pydantic model.

Tests cover valid construction, rejection of missing required fields,
default values for optional fields, custom relevance scores, and
edge-case inputs such as negative page numbers.
"""

import pytest
from pydantic import ValidationError

from due_diligence.output.schemas import Citation


class TestCitationValidData:
    """Verify that valid data parses successfully."""

    def test_valid_citation_parses(self):
        """A citation with all required fields and one optional parses."""
        data = {
            "document_name": "contract.pdf",
            "page_number": 5,
            "clause_reference": "Section 4.2(b)",
            "clause_text": "The party shall indemnify...",
            "relevance_score": 0.75,
        }
        citation = Citation(**data)
        assert citation.document_name == "contract.pdf"
        assert citation.page_number == 5
        assert citation.clause_reference == "Section 4.2(b)"
        assert citation.clause_text == "The party shall indemnify..."
        assert citation.relevance_score == 0.75


class TestCitationMissingFields:
    """Verify that omitting required fields raises ValidationError."""

    def test_missing_clause_reference_raises_error(self):
        """Omitting clause_reference should raise a ValidationError."""
        data = {
            "document_name": "contract.pdf",
            "page_number": 5,
            "clause_text": "Some text.",
        }
        with pytest.raises(ValidationError):
            Citation(**data)

    def test_missing_document_name_raises_error(self):
        """Omitting document_name should raise a ValidationError."""
        data = {
            "page_number": 5,
            "clause_reference": "Section 4.2(b)",
            "clause_text": "Some text.",
        }
        with pytest.raises(ValidationError):
            Citation(**data)


class TestCitationDefaults:
    """Verify that optional fields default correctly."""

    def test_relevance_score_defaults_to_zero(self):
        """When relevance_score is omitted it should default to 0.0."""
        data = {
            "document_name": "contract.pdf",
            "page_number": 5,
            "clause_reference": "Section 4.2(b)",
            "clause_text": "Some text.",
        }
        citation = Citation(**data)
        assert citation.relevance_score == 0.0


class TestCitationFieldValues:
    """Verify numeric field values are stored as provided."""

    def test_custom_relevance_score(self):
        """relevance_score=0.85 should be stored correctly."""
        data = {
            "document_name": "nda.pdf",
            "page_number": 12,
            "clause_reference": "Clause 7.1",
            "clause_text": "Confidential information.",
            "relevance_score": 0.85,
        }
        citation = Citation(**data)
        assert citation.relevance_score == 0.85


class TestCitationEdgeCases:
    """Verify edge-case inputs such as negative page numbers."""

    def test_negative_page_number_accepted(self):
        """Negative page_number should parse (no constraint on int field)."""
        data = {
            "document_name": "appendix.pdf",
            "page_number": -1,
            "clause_reference": "Appendix A",
            "clause_text": "Negative page test.",
        }
        citation = Citation(**data)
        assert citation.page_number == -1
