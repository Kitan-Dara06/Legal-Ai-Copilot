"""
Unit tests for app.utils.vector_utils.compute_sparse_vector.

Tests term-frequency sparse vector computation.
All I/O is mocked; no external dependencies.
"""

import pytest

from app.utils.vector_utils import compute_sparse_vector


class TestComputeSparseVector:
    """Tests for the compute_sparse_vector function."""

    def test_returns_dict_with_indices_and_values_keys(self):
        """Output must be a dict containing 'indices' and 'values'."""
        result = compute_sparse_vector("the cat")
        assert isinstance(result, dict)
        assert "indices" in result
        assert "values" in result

    def test_simple_text_returns_non_empty_arrays(self):
        """Simple text should produce at least one index-value pair."""
        result = compute_sparse_vector("the cat")
        assert isinstance(result["indices"], list)
        assert isinstance(result["values"], list)
        assert len(result["indices"]) > 0
        assert len(result["values"]) > 0

    def test_deterministic_same_text_same_output(self):
        """Same input text must always produce the same output."""
        text = "the cat sat on the mat"
        first = compute_sparse_vector(text)
        second = compute_sparse_vector(text)
        assert first == second

    def test_empty_text_returns_empty_arrays(self):
        """Empty string should return empty indices and values lists."""
        result = compute_sparse_vector("")
        assert result == {"indices": [], "values": []}

    def test_repeated_word_has_proportional_value(self):
        """
        A word repeated N times should have a frequency value of N
        for its corresponding index.
        """
        result = compute_sparse_vector("the the the")
        assert len(result["indices"]) == 1
        assert result["values"][0] == 3.0

    def test_different_words_produce_different_indices(self):
        """
        Distinct words should map to distinct indices in the sparse vector.
        """
        result = compute_sparse_vector("cat dog")
        assert len(result["indices"]) == 2
        assert result["indices"][0] != result["indices"][1]

    def test_indices_and_values_have_matching_lengths(self):
        """The indices and values arrays must always be the same length."""
        result = compute_sparse_vector("the quick brown fox jumps over the lazy dog")
        assert len(result["indices"]) == len(result["values"])

    def test_single_word_returns_single_entry(self):
        """Input with a single unique word should produce one index-value pair."""
        result = compute_sparse_vector("hello")
        assert len(result["indices"]) == 1
        assert len(result["values"]) == 1
        assert result["values"][0] == 1.0

    def test_case_insensitive_tokenization(self):
        """
        Tokens should be lowercased before hashing, so 'The' and 'the'
        map to the same index.
        """
        result_mixed = compute_sparse_vector("The the")
        result_lower = compute_sparse_vector("the the")
        assert result_mixed == result_lower

    def test_punctuation_is_stripped(self):
        """
        Punctuation characters should be stripped during tokenization,
        so 'cat.' and 'cat' map to the same index.
        """
        result_with_punct = compute_sparse_vector("cat.")
        result_clean = compute_sparse_vector("cat")
        assert result_with_punct == result_clean

    def test_non_alpha_tokens_are_hashed(self):
        """Numeric tokens like '123' should still be tokenized and hashed."""
        result = compute_sparse_vector("123 456")
        assert len(result["indices"]) == 2
        assert result["indices"][0] != result["indices"][1]
        assert result["values"] == [1.0, 1.0]

    def test_values_are_float_type(self):
        """Each value in the array must be a float."""
        result = compute_sparse_vector("the cat")
        for v in result["values"]:
            assert isinstance(v, float)

    def test_indices_are_int_type(self):
        """Each index in the array must be an int."""
        result = compute_sparse_vector("the cat")
        for idx in result["indices"]:
            assert isinstance(idx, int)

    def test_index_within_valid_range(self):
        """
        Indices should be non-negative and less than 2^31 - 1
        (compatible with Qdrant's sparse vector requirements).
        """
        result = compute_sparse_vector("a b c d e f g h i j k l m n o p")
        for idx in result["indices"]:
            assert 0 <= idx < (2**31 - 1)

    def test_long_text_does_not_explode(self):
        """A very long text should still produce a valid sparse vector."""
        long_text = "word " * 10000
        result = compute_sparse_vector(long_text)
        assert "indices" in result
        assert "values" in result
        assert len(result["indices"]) == len(result["values"])
        assert len(result["indices"]) > 0

    def test_basic_tf_counting_the_the_the_cat(self):
        """
        'the the the cat': token 'the' has value 3.0, token 'cat' has value 1.0.
        """
        result = compute_sparse_vector("the the the cat")
        # Build a dict from the parallel arrays for assertion
        kv = dict(zip(result["indices"], result["values"]))
        # 'the' appears 3 times, 'cat' appears 1 time
        # We expect exactly 2 entries
        assert len(kv) == 2
        assert 3.0 in kv.values()
        assert 1.0 in kv.values()

    def test_case_insensitivity_same_hash_value_three(self):
        """
        'Hello HELLO hello' should all map to the same index with value 3.0.
        """
        result = compute_sparse_vector("Hello HELLO hello")
        assert len(result["indices"]) == 1
        assert result["values"][0] == 3.0

    def test_unique_tokens_apple_banana_cherry(self):
        """
        'apple banana cherry' → 3 different indices, each value 1.0.
        """
        result = compute_sparse_vector("apple banana cherry")
        assert len(result["indices"]) == 3
        assert len(result["values"]) == 3
        # All three values should be 1.0 (each word appears once)
        assert all(v == 1.0 for v in result["values"])
        # All three indices should be distinct
        assert len(set(result["indices"])) == 3

    def test_special_characters_section_4_2_b(self):
        """
        'Section 4.2(b)' should be tokenized without error.
        The \\w+ pattern should capture 'Section', '4', '2', 'b'.
        """
        # Should raise no exception
        result = compute_sparse_vector("Section 4.2(b)")
        assert "indices" in result
        assert "values" in result
        # 'Section', '4', '2', 'b' = 4 tokens
        assert len(result["indices"]) == 4

    def test_long_text_1000_repeated_words(self):
        """
        1000 repetitions of the same word should produce exactly 1 index
        with value 1000.0, and not cause any error.
        """
        long_text = "repeat " * 1000
        result = compute_sparse_vector(long_text)
        assert len(result["indices"]) == 1
        assert result["values"][0] == 1000.0
