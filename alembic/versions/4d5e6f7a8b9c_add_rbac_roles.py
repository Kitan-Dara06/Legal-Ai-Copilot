"""Add new roles to userrole enum for RBAC

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2025-01-15 17:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "4d5e6f7a8b9c"
down_revision: Union[str, None] = "3c4d5e6f7a8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new roles to the userrole enum
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SYSTEM_ADMIN'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'PARTNER'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'ASSOCIATE'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values.
    # The new values will remain in the type but won't be used.
    pass
