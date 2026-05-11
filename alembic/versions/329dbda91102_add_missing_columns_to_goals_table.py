"""add missing columns to goals table

Revision ID: 329dbda91102
Revises: c3794205ec82
Create Date: 2026-05-11 11:17:00.604147

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "329dbda91102"
down_revision: Union[str, Sequence[str], None] = "c3794205ec82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create enum type for goal status (if not already exists)
    sa.Enum(
        "PENDING", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED", name="goalstatus"
    ).create(op.get_bind(), checkfirst=True)

    # Add columns to goals table
    op.add_column(
        "goals",
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="goalstatus",
            ),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "goals",
        sa.Column(
            "intent",
            sa.Enum("ANALYZE", "REASON", "ACT", "UNKNOWN", name="intenttype"),
            nullable=True,
        ),
    )
    op.add_column("goals", sa.Column("mode", sa.String(length=50), nullable=True))
    op.add_column("goals", sa.Column("answer", sa.Text(), nullable=True))
    # Remove default after backfill
    op.alter_column("goals", "status", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("goals", "answer")
    op.drop_column("goals", "mode")
    op.drop_column("goals", "intent")
    op.drop_column("goals", "status")
    sa.Enum(name="goalstatus").drop(op.get_bind(), checkfirst=True)
