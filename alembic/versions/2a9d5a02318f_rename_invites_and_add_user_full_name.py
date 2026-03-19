"""rename_invites_and_add_user_full_name

Revision ID: 2a9d5a02318f
Revises: 20250318_add_org_name_default
Create Date: 2026-03-19 11:53:30.379116

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a9d5a02318f'
down_revision: Union[str, Sequence[str], None] = '20250318_add_org_name_default'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Rename table 'invites' to 'organization_invites'
    op.rename_table('invites', 'organization_invites')
    
    # 2. Add 'full_name' to 'users'
    op.add_column('users', sa.Column('full_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Remove 'full_name' from 'users'
    op.drop_column('users', 'full_name')
    
    # 2. Rename table 'organization_invites' back to 'invites'
    op.rename_table('organization_invites', 'invites')
