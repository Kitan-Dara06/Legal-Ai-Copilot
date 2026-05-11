"""add research_log table

Revision ID: a4af6cab97fd
Revises: 329dbda91102
Create Date: 2026-05-11 21:08:21.105281

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4af6cab97fd"
down_revision: Union[str, Sequence[str], None] = "329dbda91102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "research_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column(
            "retrieved_chunks", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "reranker_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("faithfulness_score", sa.Float(), nullable=True),
        sa.Column("negation_sensitivity_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflow_executions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_research_log_goal_id"), "research_log", ["goal_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_log_workflow_id"),
        "research_log",
        ["workflow_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_research_log_workflow_id"), table_name="research_log")
    op.drop_index(op.f("ix_research_log_goal_id"), table_name="research_log")
    op.drop_table("research_log")
