"""add_role_to_organization_invites

Revision ID: 516ad88eed71
Revises: 2a9d5a02318f
Create Date: 2026-03-19 11:59:49.587526

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '516ad88eed71'
down_revision: Union[str, Sequence[str], None] = '2a9d5a02318f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Define the UserRole enum for Postgres (it might already exist from User table)
    user_role_enum = sa.Enum('ADMIN', 'MEMBER', name='userrole')
    
    # Add 'role' column to 'organization_invites'
    op.add_column('organization_invites', sa.Column('role', user_role_enum, nullable=False, server_default='MEMBER'))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove 'role' column
    op.drop_column('organization_invites', 'role')
