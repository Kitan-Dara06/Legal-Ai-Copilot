"""
Unit tests for app.dependencies.hash_api_key.

Validates SHA-256-based hashing of API keys: determinism, sensitivity
to input, output format (64-char hex), and edge cases (empty, special chars).
Pure function — no I/O, no database.
"""

import hashlib

import pytest

from app.dependencies import hash_api_key


class TestHashApiKey:
    """Suite for hash_api_key — pure SHA-256, no side effects."""

    # ── Determinism ───────────────────────────────────────────────────

    def test_same_key_twice_produces_same_hash(self):
        """Calling hash_api_key twice with the same input must return the same hash."""
        key = "sk_live_abc123"
        h1 = hash_api_key(key)
        h2 = hash_api_key(key)
        assert h1 == h2

    # ── Sensitivity ───────────────────────────────────────────────────

    def test_different_keys_produce_different_hashes(self):
        """Different inputs must yield different hash values."""
        h1 = hash_api_key("key_A")
        h2 = hash_api_key("key_B")
        assert h1 != h2

    def test_similar_keys_produce_different_hashes(self):
        """Subtly different keys (e.g., off by one character) must differ."""
        h1 = hash_api_key("sk-key-a")
        h2 = hash_api_key("sk-key-b")
        assert h1 != h2

    def test_whitespace_matters(self):
        """Leading/trailing whitespace is part of the input — not stripped."""
        h1 = hash_api_key("sk-live")
        h2 = hash_api_key(" sk-live")  # leading space
        h3 = hash_api_key("sk-live ")  # trailing space
        assert h1 != h2
        assert h1 != h3
        assert h2 != h3

    # ── Output format ─────────────────────────────────────────────────

    def test_output_is_always_64_characters(self):
        """SHA-256 hex digest must always be 64 characters long."""
        h = hash_api_key("sk_live_abc123")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_output_is_lowercase_hex(self):
        """All characters must be valid lowercase hex digits (0-9, a-f)."""
        h = hash_api_key("sk_live_abc123")
        assert all(c in "0123456789abcdef" for c in h)

    # ── Edge cases ────────────────────────────────────────────────────

    def test_empty_string_produces_valid_hash(self):
        """An empty string must not crash and must return a valid 64-char hash."""
        h = hash_api_key("")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_known_empty_string_hash(self):
        """Verify the known SHA-256 of the empty string for regression detection."""
        expected = hashlib.sha256(b"").hexdigest()
        assert hash_api_key("") == expected

    def test_key_with_special_characters(self):
        """Keys with special characters must hash without error."""
        h = hash_api_key("sk_live_abc!@#$%^&*()_+={}[]|\\:;\"'<>,.?/~`")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_long_key_produces_valid_hash(self):
        """Very long keys must be hashed without issues."""
        long_key = "sk-" + "a" * 10_000
        h = hash_api_key(long_key)
        assert len(h) == 64

    def test_unicode_key(self):
        """Unicode characters in the key must be handled (UTF-8 encoding)."""
        h = hash_api_key("sk-üñíçödé-🔑")
        assert len(h) == 64
