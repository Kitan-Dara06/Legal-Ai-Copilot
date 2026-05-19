"""merge_dual_heads

Revision ID: 743d0816411d
Revises: f1a9c3e72b08, a4af6cab97fd
Create Date: 2026-05-19 21:43:51.083733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '743d0816411d'
down_revision: Union[str, Sequence[str], None] = ('f1a9c3e72b08', 'a4af6cab97fd')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
