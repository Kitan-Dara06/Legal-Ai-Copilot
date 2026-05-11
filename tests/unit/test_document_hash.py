"""
Unit tests for SHA-256 hashing used in document deduplication.

No import needed from the app — pure hashlib operations.
The codebase pattern is:
    hashlib.sha256(data.encode("utf-8")).hexdigest()

Tests cover determinism, sensitivity, empty input, binary content,
large content, and output format.
"""

import hashlib
import os

import pytest

# Known SHA-256 hex digest of the empty string
EMPTY_STRING_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class TestDocumentHash:
    """Suite for SHA-256 hashing pattern used in document deduplication."""

    # ── Helper that mirrors the codebase pattern ──────────────────────

    @staticmethod
    def _hash_text(text: str) -> str:
        """Hash a string using the standard codebase pattern (UTF-8 encode)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_bytes(data: bytes) -> str:
        """Hash raw bytes directly."""
        return hashlib.sha256(data).hexdigest()

    # ── Determinism ───────────────────────────────────────────────────

    def test_same_bytes_produce_same_hash(self):
        """Hashing identical bytes twice must yield the same result."""
        content = b"PDF content A"
        h1 = self._hash_bytes(content)
        h2 = self._hash_bytes(content)
        assert h1 == h2

    def test_same_string_produces_same_hash(self):
        """Hashing identical text twice must yield the same result."""
        content = "PDF content A"
        h1 = self._hash_text(content)
        h2 = self._hash_text(content)
        assert h1 == h2

    # ── Sensitivity ───────────────────────────────────────────────────

    def test_different_bytes_produce_different_hash(self):
        """Different content must yield different hashes."""
        h1 = self._hash_bytes(b"A")
        h2 = self._hash_bytes(b"B")
        assert h1 != h2

    def test_single_byte_change_produces_different_hash(self):
        """A one-byte difference must produce a completely different hash."""
        h1 = self._hash_bytes(b"PDF content A")
        h2 = self._hash_bytes(b"PDF content B")
        assert h1 != h2

    def test_case_sensitivity_matters(self):
        """Different casing must produce different hashes."""
        h1 = self._hash_text("confidential")
        h2 = self._hash_text("CONFIDENTIAL")
        assert h1 != h2

    def test_whitespace_matters(self):
        """Leading/trailing whitespace is not stripped — it changes the hash."""
        h1 = self._hash_text("clause")
        h2 = self._hash_text(" clause")
        h3 = self._hash_text("clause ")
        assert h1 != h2
        assert h1 != h3

    # ── Empty input ───────────────────────────────────────────────────

    def test_empty_bytes_produces_known_hash(self):
        """The SHA-256 of b'' is a well-known constant."""
        assert self._hash_bytes(b"") == EMPTY_STRING_SHA256

    def test_empty_string_produces_known_hash(self):
        """The SHA-256 of '' is a well-known constant."""
        assert self._hash_text("") == EMPTY_STRING_SHA256

    # ── Binary content ────────────────────────────────────────────────

    def test_binary_content_works(self):
        """Raw bytes must hash correctly without requiring encoding."""
        binary_data = b"\x00\x01\x02\xff\xfe"
        h = self._hash_bytes(binary_data)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_binary_with_null_bytes(self):
        """Binary containing null bytes must hash without error."""
        binary_data = b"legal\x00clause\x00\xff"
        h = self._hash_bytes(binary_data)
        assert len(h) == 64

    def test_pdf_like_binary_content(self):
        """Simulate hashing raw PDF content (header + binary stream)."""
        pdf_header = b"%PDF-1.4\n"
        binary_stream = bytes(range(256))
        content = pdf_header + binary_stream
        h = self._hash_bytes(content)
        assert len(h) == 64

    # ── Large content ─────────────────────────────────────────────────

    def test_large_content_produces_valid_hash(self):
        """1 MB of random bytes must hash without error and produce a 64-char hash."""
        large_content = os.urandom(1_048_576)  # 1 MB
        h = self._hash_bytes(large_content)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_large_string_produces_valid_hash(self):
        """Very large strings must hash correctly."""
        large_text = "Lorem ipsum dolor sit amet. " * 10_000
        h = self._hash_text(large_text)
        assert len(h) == 64

    # ── Output format ─────────────────────────────────────────────────

    def test_hash_is_64_character_hex_string(self):
        """SHA-256 hex digest must always be 64 characters long."""
        h = self._hash_text("This legal document is confidential.")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_hash_is_lowercase_hex(self):
        """All characters must be valid lowercase hex digits (0-9, a-f)."""
        h = self._hash_text("Non-Disclosure Agreement")
        assert all(c in "0123456789abcdef" for c in h)

    # ── Unicode ───────────────────────────────────────────────────────

    def test_unicode_content(self):
        """Unicode / multi-byte characters must hash correctly via UTF-8 encoding."""
        h = self._hash_text("Contrato de confidencialidad — © 2024")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)
