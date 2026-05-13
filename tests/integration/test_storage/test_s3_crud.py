"""
Integration tests for Cloudflare R2 (S3-compatible) object storage.

FR-L3-01 / Priority 1: Upload, existence check, and deletion of objects
via the app.services.object_storage module.
"""

import pytest

from app.services.object_storage import (
    delete_file,
    object_exists,
    upload_bytes,
)

pytestmark = pytest.mark.integration


class TestS3CRUD:
    """Covers the full CRUD lifecycle of objects in R2 object storage."""

    def test_upload_bytes_returns_r2_uri(self, test_blob_name, test_content):
        """upload_bytes should return a string starting with 'r2://'."""
        result = upload_bytes(test_content, test_blob_name)

        assert isinstance(result, str)
        assert result.startswith("r2://")

    def test_object_exists_after_upload(self, test_blob_name, test_content):
        """object_exists should return True immediately after upload."""
        upload_bytes(test_content, test_blob_name)

        assert object_exists(test_blob_name) is True

    def test_delete_file_works(self, test_blob_name, test_content):
        """delete_file should remove the object without raising."""
        upload_bytes(test_content, test_blob_name)

        # Should not raise
        delete_file(test_blob_name)

        # Confirm it's gone
        assert object_exists(test_blob_name) is False

    def test_delete_nonexistent_does_not_raise(self):
        """delete_file on a non-existent key should silently succeed."""
        # Should not raise
        delete_file("nonexistent")

    def test_object_exists_nonexistent_returns_false(self):
        """object_exists should return False for a non-existent key."""
        assert object_exists("nonexistent") is False

    def test_upload_and_verify_content_indirectly(self, test_blob_name, test_content):
        """
        Upload bytes, verify the object exists, delete it, and confirm
        the object is gone — full lifecycle.
        """
        # Upload
        uri = upload_bytes(test_content, test_blob_name)
        assert uri.startswith("r2://")

        # Exists
        assert object_exists(test_blob_name) is True

        # Delete
        delete_file(test_blob_name)

        # Gone
        assert object_exists(test_blob_name) is False
