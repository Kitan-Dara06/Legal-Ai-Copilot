"""Add error_context to workflow_executions

Revision ID: a3f7b2c9d1e4
Revises: f1a9c3e72b08
Create Date: 2026-06-09

Changes:
  1. Add error_context (Text, nullable) to workflow_executions
     — stores human-readable failure context when status=FAILED.
     Required by the process_workflow safety net and stale_task_sweeper.

  2. Set error_context server_default=NULL (no data migration needed —
     existing FAILED rows will simply have NULL until the next failure cycle).
"""

from alembic import op
import sqlalchemy as sa

revision = "a3f7b2c9d1e4"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_executions",
        sa.Column(
            "error_context",
            sa.Text(),
            nullable=True,
            comment="Human-readable error context set when status=FAILED",
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_executions", "error_context")
