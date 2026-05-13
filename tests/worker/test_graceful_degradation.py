"""
Tests for graceful degradation when infrastructure is unavailable.

Workers must handle transient failures (Neo4j down, Gemini unreachable,
DB connection lost) without crashing the entire pipeline.  Retry policies
and exception handling are the primary mechanisms.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestGracefulDegradation:
    """Pipeline must handle missing infrastructure without crashing."""

    # ── Ingestion pipeline resilience ─────────────────────────────────

    @patch("app.tasks.download_file_from_gcs")
    @patch("app.tasks._process_document_core")
    def test_ingestion_continues_when_neo4j_down(
        self,
        mock_process_core,
        mock_download,
        sample_doc_id,
        sample_workspace_id,
        sample_org_id,
    ):
        """Neo4j failure should not crash the entire pipeline."""
        from app.tasks import process_digital_pdf

        # Mock self (bound Celery task)
        mock_self = MagicMock()
        mock_self.max_retries = 3
        mock_self.default_retry_delay = 60

        # Simulate _process_document_core failing (Neo4j down)
        mock_process_core.side_effect = RuntimeError("Neo4j connection refused")

        # Mock download to create a dummy file so the temp-path logic works
        def fake_download(blob_name, dest_path):
            with open(dest_path, "w") as f:
                f.write("mock pdf content")

        mock_download.side_effect = fake_download

        # Execute — the task should raise self.retry (simulated)
        with patch.object(mock_self, "retry", side_effect=RuntimeError("RETRY")):
            with pytest.raises(RuntimeError, match="RETRY"):
                process_digital_pdf(
                    mock_self,
                    str(sample_doc_id),
                    str(sample_workspace_id),
                    str(sample_org_id),
                    "test.pdf",
                    "test_blob.pdf",
                )

        # Verify the core pipeline was attempted
        assert mock_process_core.called, "_process_document_core was never called"

    @patch("app.tasks.download_file_from_gcs")
    @patch("app.tasks._process_document_core")
    def test_ingestion_handles_lease_exhausted(
        self,
        mock_process_core,
        mock_download,
        sample_doc_id,
        sample_workspace_id,
        sample_org_id,
    ):
        """LeaseExhaustedError (LLM concurrency limit) triggers retry."""
        from app.tasks import LeaseExhaustedError, process_digital_pdf

        mock_self = MagicMock()
        mock_self.max_retries = 3
        mock_self.default_retry_delay = 60

        # Simulate LLM concurrency limit
        mock_process_core.side_effect = LeaseExhaustedError("No free LLM slots")

        def fake_download(blob_name, dest_path):
            with open(dest_path, "w") as f:
                f.write("mock pdf")

        mock_download.side_effect = fake_download

        with patch.object(mock_self, "retry", side_effect=RuntimeError("RETRY")):
            with pytest.raises(RuntimeError, match="RETRY"):
                process_digital_pdf(
                    mock_self,
                    str(sample_doc_id),
                    str(sample_workspace_id),
                    str(sample_org_id),
                    "test_lease.pdf",
                    "lease_blob.pdf",
                )

        assert mock_process_core.called

    # ── Retry policy configuration ────────────────────────────────────

    def test_digital_pdf_retry_policy_configured(self):
        """process_digital_pdf should have retry configured."""
        from app.tasks import process_digital_pdf

        assert process_digital_pdf.max_retries is not None
        assert process_digital_pdf.max_retries >= 2
        assert process_digital_pdf.default_retry_delay >= 30

    def test_scanned_pdf_retry_policy_configured(self):
        """process_scanned_pdf should have retry configured."""
        from app.tasks import process_scanned_pdf

        assert process_scanned_pdf.max_retries is not None
        assert process_scanned_pdf.max_retries >= 1
        assert process_scanned_pdf.default_retry_delay >= 30

    def test_deadline_scanner_retry_policy_configured(self):
        """deadline_scanner should have retry configured."""
        from app.tasks import deadline_scanner

        assert deadline_scanner.max_retries is not None
        assert deadline_scanner.max_retries >= 2

    def test_cleanup_stale_data_has_no_retry(self):
        """cleanup_stale_data is fire-and-forget; retry is optional."""
        from app.tasks import cleanup_stale_data

        # It may not have retry attributes at all since it has no
        # explicit max_retries in the decorator
        assert getattr(cleanup_stale_data, "max_retries", None) is None

    # ── Scanner resilience ────────────────────────────────────────────

    @patch("app.tasks.get_pg_pool")
    def test_deadline_scanner_handles_db_failure(self, mock_get_pool):
        """deadline_scanner should retry on DB failure."""
        from app.tasks import deadline_scanner

        mock_pool = MagicMock()
        # Calling getconn on the pool fails
        mock_pool.getconn.side_effect = RuntimeError("Postgres connection lost")
        mock_get_pool.return_value = mock_pool

        mock_self = MagicMock()
        mock_self.max_retries = 3
        mock_self.default_retry_delay = 60

        with patch.object(mock_self, "retry", side_effect=RuntimeError("RETRY")):
            with pytest.raises(RuntimeError, match="RETRY"):
                deadline_scanner(mock_self)

        assert mock_pool.getconn.called

    @patch("app.tasks.get_pg_pool")
    def test_deadline_scanner_returns_conn_on_error(self, mock_get_pool):
        """Connection should be returned to pool even on failure."""
        from app.tasks import deadline_scanner

        mock_cursor = MagicMock()
        # Simulate a query failure
        mock_cursor.execute.side_effect = [
            None,  # First SELECT succeeds
            RuntimeError("UPDATE failed"),  # UPDATE fails
        ]
        mock_cursor.fetchall.return_value = [
            (
                "dl_001",
                datetime.now(timezone.utc),
                0.5,
                "desc",
                "ws",
                "org",
            ),
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_pool = MagicMock()
        mock_pool.getconn.return_value = mock_conn
        mock_get_pool.return_value = mock_pool

        mock_self = MagicMock()
        mock_self.max_retries = 3
        mock_self.default_retry_delay = 60

        with patch.object(mock_self, "retry", side_effect=RuntimeError("RETRY")):
            with pytest.raises(RuntimeError, match="RETRY"):
                deadline_scanner(mock_self)

        # Connection must be returned even after failure
        assert mock_pool.putconn.called
        assert mock_conn.rollback.called
