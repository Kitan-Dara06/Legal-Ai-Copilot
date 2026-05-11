"""
Unit tests for the IntentClassification Pydantic model.

Tests cover construction from valid data for all three intents,
rejection of missing required fields, tolerance of unknown intent
strings, edge-case numeric values, and default field behaviour.
"""

import pytest
from pydantic import ValidationError

from app.services.agent.nodes import IntentClassification


class TestIntentClassificationValid:
    """Verify that each valid intent parses successfully."""

    def test_valid_analyze(self):
        """ANALYZE intent with all fields parses successfully."""
        data = {
            "primary_intent": "ANALYZE",
            "confidence": 0.95,
            "reasoning": "Test",
            "requires_graph": False,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": 0.0,
        }
        model = IntentClassification(**data)
        assert model.primary_intent == "ANALYZE"
        assert model.confidence == 0.95
        assert model.reasoning == "Test"
        assert model.requires_graph is False
        assert model.requires_action is False
        assert model.deadline_sensitive is False
        assert model.urgency_score == 0.0

    def test_valid_reason(self):
        """REASON intent with typical values parses successfully."""
        data = {
            "primary_intent": "REASON",
            "confidence": 0.80,
            "reasoning": "Cross-document reasoning needed",
            "requires_graph": True,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": 0.0,
        }
        model = IntentClassification(**data)
        assert model.primary_intent == "REASON"
        assert model.requires_graph is True
        assert model.requires_action is False

    def test_valid_act(self):
        """ACT intent with execution-related values parses successfully."""
        data = {
            "primary_intent": "ACT",
            "confidence": 0.99,
            "reasoning": "Execution required",
            "requires_graph": False,
            "requires_action": True,
            "deadline_sensitive": True,
            "urgency_score": 0.8,
        }
        model = IntentClassification(**data)
        assert model.primary_intent == "ACT"
        assert model.requires_action is True
        assert model.deadline_sensitive is True
        assert model.urgency_score == 0.8


class TestIntentClassificationMissingFields:
    """Verify that omitting required fields raises ValidationError."""

    def test_missing_confidence_raises_error(self):
        """Omitting confidence should raise a ValidationError."""
        data = {
            "primary_intent": "ANALYZE",
            "reasoning": "test",
            "requires_graph": False,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": 0.0,
        }
        with pytest.raises(ValidationError):
            IntentClassification(**data)

    def test_missing_primary_intent_raises_error(self):
        """Omitting primary_intent should raise a ValidationError."""
        data = {
            "confidence": 0.95,
            "reasoning": "test",
            "requires_graph": False,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": 0.0,
        }
        with pytest.raises(ValidationError):
            IntentClassification(**data)


class TestIntentClassificationEdgeCases:
    """Verify behaviour with unusual but permissible inputs."""

    def test_unknown_intent_string_accepted(self):
        """primary_intent='INVALID' should parse (str is unconstrained)."""
        data = {
            "primary_intent": "INVALID",
            "confidence": 0.90,
            "reasoning": "Edge case",
            "requires_graph": False,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": 0.0,
        }
        model = IntentClassification(**data)
        assert model.primary_intent == "INVALID"

    def test_negative_urgency_score_accepted(self):
        """urgency_score=-0.5 should parse (no ge/le constraint on field)."""
        data = {
            "primary_intent": "ANALYZE",
            "confidence": 0.90,
            "reasoning": "Negative urgency",
            "requires_graph": False,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": -0.5,
        }
        model = IntentClassification(**data)
        assert model.urgency_score == -0.5

    def test_all_optional_defaults(self):
        """Only required fields should produce defaults for the rest.

        Required fields: primary_intent, confidence, reasoning,
        requires_graph, requires_action, deadline_sensitive, urgency_score.
        Since every field is required (no Field(default=...)), providing
        all of them is mandatory. This test verifies that when all fields
        are provided, the model is constructed without error.
        """
        data = {
            "primary_intent": "ANALYZE",
            "confidence": 0.5,
            "reasoning": "Defaults check",
            "requires_graph": False,
            "requires_action": False,
            "deadline_sensitive": False,
            "urgency_score": 0.0,
        }
        model = IntentClassification(**data)
        assert model.primary_intent == "ANALYZE"
        assert model.confidence == 0.5
        assert model.reasoning == "Defaults check"
