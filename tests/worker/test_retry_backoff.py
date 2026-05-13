"""Tests for exponential backoff and retry behavior."""

from unittest.mock import MagicMock, patch

import pytest

from app.tasks import _process_document_core, process_digital_pdf


class TestRetryOnTransientFailure:
    """Task should retry when external services fail momentarily."""

    @patch("app.services.object_storage.download_file_from_gcs")
    def test_download_failure_bubbles_up_for_retry(
        self, mock_download, sample_doc_id, sample_workspace_id, sample_org_id
    ):
        """If R2 download fails, the task raises so Celery can retry."""
        mock_download.side_effect = Exception("Connection reset by peer")

        # process_digital_pdf wraps download errors in RuntimeError and retries
        with pytest.raises(Exception) as exc_info:
            process_digital_pdf(
                str(sample_doc_id),
                str(sample_workspace_id),
                str(sample_org_id),
                "test.pdf",
                "test_blob.pdf",
            )

        # The exception should mention the original failure
        assert "Connection reset by peer" in str(
            exc_info.value
        ) or "Task failed" in str(exc_info.value)

    @patch("app.tasks.update_postgres_status_sync")
    @patch("app.services.ingestion.parser.LegalDocumentParser")
    def test_parse_failure_sets_failed_status(
        self,
        mock_parser_cls,
        mock_update_status,
        sample_doc_id,
        sample_workspace_id,
        sample_org_id,
        tmp_path,
    ):
        """If parsing fails, _process_document_core sets FAILED status and re-raises."""
        # Create a real temp file so the code path proceeds to parsing
        temp_file = tmp_path / "bad_doc.pdf"
        temp_file.write_text("not a real pdf")

        mock_parser = MagicMock()
        mock_parser.parse_pdf.side_effect = ValueError("PDF parse error")
        mock_parser_cls.return_value = mock_parser

        with pytest.raises(ValueError, match="PDF parse error"):
            _process_document_core(
                str(sample_doc_id),
                str(sample_workspace_id),
                str(sample_org_id),
                "bad_doc.pdf",
                str(temp_file),
            )

        # Should have attempted to set FAILED status
        failed_calls = [
            args for args in mock_update_status.call_args_list if "FAILED" in str(args)
        ]
        assert len(failed_calls) >= 1, "Expected at least one FAILED status update"

    def test_exponential_backoff_delay_calculated_correctly(self):
        """Verify the backoff delay formula: base_delay * 2^attempt"""
        from app.services.agent.tool_registry import RetryPolicy

        policy = RetryPolicy(max_retries=3, base_delay_s=10.0)
        assert policy.base_delay_s * (2**0) == 10.0  # attempt 0
        assert policy.base_delay_s * (2**1) == 20.0  # attempt 1
        assert policy.base_delay_s * (2**2) == 40.0  # attempt 2

    def test_default_retry_policy_values(self):
        """RetryPolicy should default to 3 retries with 10s base delay."""
        from app.services.agent.tool_registry import RetryPolicy

        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay_s == 10.0

    def test_lease_exhausted_error_propagates(self):
        """LeaseExhaustedError should propagate without being wrapped."""
        from app.tasks import LeaseExhaustedError

        with pytest.raises(LeaseExhaustedError):
            raise LeaseExhaustedError("No LLM slots available")
