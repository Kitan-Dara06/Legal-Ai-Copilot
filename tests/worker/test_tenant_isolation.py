"""
Tests for tenant isolation in Celery workers.

Worker context (DB connections, Redis state, cached references) must not
leak between different organisations or workspaces.  Idempotency keys
derived from different scopes must be distinct.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestTenantIsolation:
    """Worker context must not leak between different orgs."""

    # ── Task-level isolation ──────────────────────────────────────────

    def test_different_orgs_get_independent_context(self):
        """Running ping tasks sequentially should not cross-contaminate."""
        from app.tasks import ping

        result_a = ping()
        result_b = ping()

        # Both should succeed independently
        assert result_a == {"ok": True}
        assert result_b == {"ok": True}

    @pytest.mark.skipif(
        True,
        reason="Needs a running Celery worker / broker; "
        "test is here as a structural placeholder",
    )
    def test_concurrent_tasks_from_different_orgs(self):
        """
        Reserve-provisioning should not collide.

        This test requires a real broker and worker to verify that Org A's
        task does not accidentally read or overwrite Org B's data.
        """
        # TODO: implement integration test with pytest-celery or test
        #       broker when running against a real RabbitMQ instance.
        pass

    # ── Idempotency key derivation (FR-EXEC-02) ───────────────────────

    def test_idempotency_keys_are_deterministic(self):
        """Same inputs always produce the same key."""
        from app.services.agent.nodes import generate_idempotency_key

        key = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        key_repeat = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)

        assert key == key_repeat

    def test_idempotency_key_is_sha256_length(self):
        """Key must be a 64-char hex string (SHA-256 digest)."""
        from app.services.agent.nodes import generate_idempotency_key

        key = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_different_workflow_different_key(self):
        """Keys from different workflow IDs must differ."""
        from app.services.agent.nodes import generate_idempotency_key

        key_a = generate_idempotency_key("wf_a", "action_1", "SEND_NOTICE", 1)
        key_b = generate_idempotency_key("wf_b", "action_1", "SEND_NOTICE", 1)
        assert key_a != key_b

    def test_different_action_different_key(self):
        """Keys from different action IDs must differ."""
        from app.services.agent.nodes import generate_idempotency_key

        key_a = generate_idempotency_key("wf_1", "act_a", "SEND_NOTICE", 1)
        key_b = generate_idempotency_key("wf_1", "act_b", "SEND_NOTICE", 1)
        assert key_a != key_b

    def test_different_tool_different_key(self):
        """Keys from different tool names must differ."""
        from app.services.agent.nodes import generate_idempotency_key

        key_a = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        key_b = generate_idempotency_key("wf_1", "action_1", "FILE_DOC", 1)
        assert key_a != key_b

    def test_different_attempt_different_key(self):
        """Keys from different attempt numbers must differ."""
        from app.services.agent.nodes import generate_idempotency_key

        key_1 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        key_2 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 2)
        assert key_1 != key_2

    # ── Redis key isolation (structural) ──────────────────────────────

    def test_progress_key_format_includes_document_id(self, monkeypatch):
        """Progress keys should be scoped by document ID."""
        import app.tasks as tasks
        from app.tasks import update_progress_sync

        mock_redis = MagicMock()
        monkeypatch.setattr(tasks, "get_redis_conn", lambda: mock_redis)

        update_progress_sync("doc-aaaa", 50)
        update_progress_sync("doc-bbbb", 75)

        # Each document gets its own key
        set_args = [c.args for c in mock_redis.set.call_args_list]
        keys_used = {args[0] for args in set_args}

        assert "progress:doc-aaaa" in keys_used
        assert "progress:doc-bbbb" in keys_used
        assert len(keys_used) == 2, "Each document should have a separate progress key"

    # ── Parameter isolation ───────────────────────────────────────────

    @patch("app.tasks.download_file_from_gcs")
    @patch("app.tasks._process_document_core")
    @patch("app.tasks.os.remove")
    def test_org_scoped_tasks_use_correct_parameters(
        self, mock_remove, mock_core, mock_dl
    ):
        """
        Task parameters (org_id, workspace_id) are passed through to
        the core processing; they are not mixed up between calls.
        """
        from app.tasks import process_digital_pdf

        mock_self = MagicMock()

        def fake_download(blob_name, dest_path):
            with open(dest_path, "w") as f:
                f.write("mock")

        mock_dl.side_effect = fake_download

        # Call the task with org-specific params
        process_digital_pdf(
            mock_self,
            "doc_org_a",
            "ws_org_a",
            "org_a",
            "report.pdf",
            "blob_a.pdf",
        )

        # Verify the correct parameters reached _process_document_core
        call_args = mock_core.call_args
        assert call_args is not None
        # _process_document_core(document_id, workspace_id, org_id, ...)
        assert call_args[0][0] == "doc_org_a"
        assert call_args[0][1] == "ws_org_a"
        assert call_args[0][2] == "org_a"
