"""Add document_subset, context, last_active_at to workspace_sessions

Revision ID: 1a2b3c4d5e6f
Revises: ad18522944c1
Create Date: 2025-01-15 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, None] = "ad18522944c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "workspace_sessions",
        sa.Column(
            "document_subset",
            postgresql.ARRAY(sa.Uuid()),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_sessions",
        sa.Column("context", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "workspace_sessions",
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workspace_sessions", "last_active_at")
    op.drop_column("workspace_sessions", "context")
    op.drop_column("workspace_sessions", "document_subset")
