"""Add immutability trigger to audit_log table for NFR-SEC-05

Revision ID: 5e6f7a8b9c0d
Revises: 8a1a8212fdd4
Create Date: 2026-05-06 21:30:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e6f7a8b9c0d"
down_revision: Union[str, None] = "4d5e6f7a8b9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NFR-SEC-05: Enforce append-only semantics on audit_log
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutations()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'Audit log is immutable (NFR-SEC-05)';
            ELSIF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Audit log is immutable (NFR-SEC-05)';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER audit_log_immutability_trigger
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutations();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutability_trigger ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutations()")
