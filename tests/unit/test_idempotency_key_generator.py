"""
Unit tests for app.services.agent.nodes.generate_idempotency_key.

Validates determinism, sensitivity to each input field, output format (SHA-256),
and edge cases. Pure function — no I/O, no database.
"""

import hashlib

import pytest

from app.services.agent.nodes import generate_idempotency_key


class TestGenerateIdempotencyKey:
    """Suite for generate_idempotency_key — deterministic SHA-256, no side effects."""

    # ── Determinism ───────────────────────────────────────────────────

    def test_same_inputs_produce_same_hash(self):
        """Calling twice with identical arguments must return identical keys."""
        k1 = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 1)
        k2 = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 1)
        assert k1 == k2

    # ── Sensitivity to each parameter ─────────────────────────────────

    def test_different_attempt_produces_different_hash(self):
        """Changing attempt_number alone must yield a different key."""
        k1 = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 1)
        k2 = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 2)
        assert k1 != k2

    def test_different_tool_produces_different_hash(self):
        """Changing tool_name alone must yield a different key."""
        k1 = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 1)
        k2 = generate_idempotency_key("wf-001", "act-42", "SEND_MAIL", 1)
        assert k1 != k2

    def test_different_action_id_produces_different_hash(self):
        """Changing action_id alone must yield a different key."""
        k1 = generate_idempotency_key("wf-001", "act-1", "FILE_DOC", 1)
        k2 = generate_idempotency_key("wf-001", "act-2", "FILE_DOC", 1)
        assert k1 != k2

    def test_different_workflow_produces_different_hash(self):
        """Changing workflow_id alone must yield a different key."""
        k1 = generate_idempotency_key("wf-A", "act-42", "FILE_DOC", 1)
        k2 = generate_idempotency_key("wf-B", "act-42", "FILE_DOC", 1)
        assert k1 != k2

    # ── Output format ─────────────────────────────────────────────────

    def test_output_is_valid_sha256_length(self):
        """SHA-256 hex digest must be exactly 64 characters."""
        key = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 1)
        assert isinstance(key, str)
        assert len(key) == 64

    def test_output_is_lowercase_hex(self):
        """All characters must be valid lowercase hex digits (0-9, a-f)."""
        key = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 1)
        assert all(c in "0123456789abcdef" for c in key)

    # ── Edge cases ────────────────────────────────────────────────────

    def test_empty_strings_produce_valid_hash(self):
        """Empty strings for workflow_id and action_id must still produce a valid hash."""
        key = generate_idempotency_key("", "", "FILE_DOC", 1)
        assert isinstance(key, str)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_zero_attempt_number_is_valid(self):
        """attempt_number=0 must not crash and must return a valid hash."""
        key = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 0)
        assert len(key) == 64

    def test_large_attempt_number_is_valid(self):
        """High attempt_number (e.g., 999) must be handled without error."""
        key = generate_idempotency_key("wf-001", "act-42", "FILE_DOC", 999)
        assert len(key) == 64

    def test_special_characters_in_tool_name(self):
        """Tool names with special characters must still produce a valid hash."""
        key = generate_idempotency_key("wf-001", "act-42", "tool_with_!@#$%^&*()", 1)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_long_workflow_id(self):
        """Very long workflow_id must produce a valid hash without error."""
        key = generate_idempotency_key("wf-" + "a" * 10_000, "act-42", "FILE_DOC", 1)
        assert len(key) == 64
