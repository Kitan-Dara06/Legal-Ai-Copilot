"""Tests for the document ingestion pipeline happy path."""

from unittest.mock import MagicMock, patch

import pytest

from app.tasks import process_digital_pdf


class TestIngestionPipeline:
    """Happy path: digital PDF ingestion completes successfully."""

    @patch("app.services.object_storage.download_file_from_gcs")
    @patch("app.services.object_storage.delete_file_from_gcs")
    @patch("app.services.ingestion.parser.LegalDocumentParser")
    @patch("app.services.ingestion.chunker.ClauseChunker")
    @patch(
        "app.tasks.run_intelligence_pipeline_async",
        return_value={"embed": True, "terms": True},
    )
    @patch("app.tasks.update_postgres_status_sync")
    def test_digital_pdf_completes_successfully(
        self,
        mock_update_status,
        mock_pipeline,
        mock_chunker_cls,
        mock_parser_cls,
        mock_delete,
        mock_download,
        sample_doc_id,
        sample_workspace_id,
        sample_org_id,
    ):
        """process_digital_pdf should update status to READY on success."""
        # Mock parser — returns a list of raw blocks
        mock_parser = MagicMock()
        mock_parser.body_font_size = 12.0
        mock_parser.parse_pdf.return_value = [
            {"page": 1, "text": "This is a test contract paragraph."},
            {"page": 1, "text": "Party A agrees to indemnify Party B."},
        ]
        mock_parser_cls.return_value = mock_parser

        # Mock chunker — returns a list of chunk dicts
        mock_chunker = MagicMock()
        mock_chunker.build_chunks.return_value = [
            {
                "chunk_text": "This is a test contract paragraph.",
                "page_number": 1,
                "section": "ARTICLE 1",
            },
            {
                "chunk_text": "Party A agrees to indemnify Party B.",
                "page_number": 1,
                "section": "ARTICLE 2",
            },
        ]
        mock_chunker_cls.return_value = mock_chunker

        # Mock R2 download to create a real temp file
        def fake_download(blob_name, dest_path):
            with open(dest_path, "w") as f:
                f.write("mock pdf content")

        mock_download.side_effect = fake_download

        # Execute task synchronously (task_always_eager=True)
        result = process_digital_pdf(
            str(sample_doc_id),
            str(sample_workspace_id),
            str(sample_org_id),
            "test_doc.pdf",
            "test_blob.pdf",
        )

        # Assertions
        assert result is not None
        # Status should have been updated to PROCESSING then READY
        status_calls = [
            c[0][0] if isinstance(c[0], tuple) else c[0]
            for c in mock_update_status.call_args_list
        ]
        # _process_document_core calls update_postgres_status_sync with "READY" on success
        assert any("READY" in str(args) for args in mock_update_status.call_args_list)

        # Parser should have been instantiated and called
        mock_parser_cls.assert_called_once()
        mock_parser.parse_pdf.assert_called_once()

        # Chunker should have been built and called
        mock_chunker_cls.assert_called_once_with(body_font_size=12.0)
        mock_chunker.build_chunks.assert_called_once()

        # Intelligence pipeline should have run
        mock_pipeline.assert_awaited_once()

        # Cleanup: R2 blob should have been deleted
        mock_delete.assert_called_once_with("test_blob.pdf")
