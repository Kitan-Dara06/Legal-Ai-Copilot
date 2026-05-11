"""Merge RBAC + draft_payload branches

Revision ID: c3794205ec82
Revises: 5e6f7a8b9c0d, 8a1a8212fdd4
Create Date: 2026-05-08 00:18:30.155519

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3794205ec82'
down_revision: Union[str, Sequence[str], None] = ('5e6f7a8b9c0d', '8a1a8212fdd4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
