"""Add organizations.name and created_at default.

Revision ID: 20250318_add_org_name_default
Revises: 20240914_multi_org
Create Date: 2025-03-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20250318_add_org_name_default"
down_revision = "20240914_multi_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add name column if missing
    op.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS name VARCHAR(255)")

    # Ensure created_at has a default
    op.execute(
        "ALTER TABLE organizations ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP"
    )

    # Backfill created_at where null
    op.execute(
        "UPDATE organizations SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
    )


def downgrade() -> None:
    # Remove default and drop name column (safe rollback)
    op.execute("ALTER TABLE organizations ALTER COLUMN created_at DROP DEFAULT")
    op.execute("ALTER TABLE organizations DROP COLUMN IF EXISTS name")
