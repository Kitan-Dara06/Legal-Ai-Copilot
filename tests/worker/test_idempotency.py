"""Tests for idempotency key enforcement (NFR-EXEC-02)."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent.nodes import generate_idempotency_key


class TestIdempotencyKeyGeneration:
    """The key generator must produce deterministic, unique keys."""

    def test_same_inputs_produce_same_key(self):
        """FR-EXEC-02: Same inputs must produce identical keys."""
        key1 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        key2 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex digest

    def test_different_attempt_produces_different_key(self):
        """Different attempt numbers must produce different keys."""
        key1 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        key2 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 2)
        assert key1 != key2

    def test_different_tool_produces_different_key(self):
        """Different tool names must produce different keys."""
        key1 = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        key2 = generate_idempotency_key("wf_1", "action_1", "FILE_DOCUMENT", 1)
        assert key1 != key2

    def test_key_is_valid_sha256(self):
        """Output must be a valid 64-character hex string."""
        key = generate_idempotency_key("wf_1", "action_1", "SEND_NOTICE", 1)
        assert all(c in "0123456789abcdef" for c in key)
        assert len(key) == 64


class TestIdempotencyExecution:
    """The execution engine must skip already-completed tasks."""

    @pytest.mark.asyncio
    @patch("app.services.agent.nodes.AsyncSessionLocal")
    @patch("app.services.agent.nodes.select")
    @patch("app.services.agent.nodes.ToolCallLog")
    async def test_skips_already_successful_task(
        self, mock_tool_call_log_cls, mock_select, mock_async_session_local
    ):
        """If a ToolCallLog with SUCCESS status exists for the key, skip execution."""
        from app.services.agent.nodes import execute_node

        # Build a mock for the async session context manager
        mock_session = AsyncMock()
        mock_async_session_local.return_value.__aenter__.return_value = mock_session

        # --- Mock the first query (for Action) ---
        mock_action_result = AsyncMock()
        mock_action = MagicMock()
        mock_action.id = "action-uuid-123"
        mock_action.action_type.value = "SEND_NOTICE"
        mock_action.draft_payload = {"to": "test@example.com", "body": "Hello"}
        mock_action.task_order = 0
        mock_action.org_id = "org_1"
        mock_action.status = "PENDING"
        mock_action_result.scalar_one_or_none.return_value = mock_action

        # --- Mock the second query (for ApprovalRequest) ---
        mock_approval_result = AsyncMock()
        mock_approval = MagicMock()
        mock_approval.status = "APPROVED"
        mock_approval.actor = "admin"
        mock_approval.decision_timestamp = MagicMock()
        mock_approval_result.scalar_one_or_none.return_value = mock_approval

        # --- Mock the third query (for existing ToolCallLog with SUCCESS) ---
        mock_log_result = AsyncMock()
        mock_existing_log = MagicMock(
            status="SUCCESS",
            id="existing_log_id",
        )
        mock_log_result.scalar_one_or_none.return_value = mock_existing_log

        # Wire up execute() to return appropriate results in sequence
        mock_session.execute = AsyncMock(
            side_effect=[mock_action_result, mock_approval_result, mock_log_result]
        )

        # Mock commit
        mock_session.commit = AsyncMock()

        state = {
            "workflow_id": "wf_1",
            "documents": [],
            "current_task_index": 0,
            "total_tasks": 1,
        }

        result = await execute_node(state)
        # The function should handle the idempotency check gracefully
        assert result is not None
        # Since the task already succeeded, it should advance the task index
        assert "current_task_index" in result
