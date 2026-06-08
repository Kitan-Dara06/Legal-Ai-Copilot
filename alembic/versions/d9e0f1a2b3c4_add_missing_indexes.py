"""Add missing indexes for scaling.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-01

Adds indexes on hot columns that are missing from the initial schema:
  - workflow_executions(org_id) — queried on every status poll
  - workflow_executions(status) — queried by stale_task_sweeper
  - goals(org_id) — queried by list_goals and cancel_goal
  - documents(status) — queried by stale_task_sweeper
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_workflow_executions_org_id",
        "workflow_executions",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_executions_status",
        "workflow_executions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_goals_org_id",
        "goals",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        "ix_documents_status",
        "documents",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_goals_org_id", table_name="goals")
    op.drop_index("ix_workflow_executions_status", table_name="workflow_executions")
    op.drop_index("ix_workflow_executions_org_id", table_name="workflow_executions")
