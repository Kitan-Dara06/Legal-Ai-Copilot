"""merge_decision_brief_into_main

Revision ID: 90b231422763
Revises: 743d0816411d, a7f3b2e91c05
Create Date: 2026-05-27 21:57:05.901693

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90b231422763'
down_revision: Union[str, Sequence[str], None] = ('743d0816411d', 'a7f3b2e91c05')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
