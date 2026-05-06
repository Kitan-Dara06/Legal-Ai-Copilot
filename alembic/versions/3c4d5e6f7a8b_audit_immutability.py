"""Add audit immutability triggers (NFR-SEC-05)

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2025-01-15 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers
revision: str = "3c4d5e6f7a8b"
down_revision: Union[str, None] = "2b3c4d5e6f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NFR-SEC-05: Block UPDATE and DELETE on audit_log
    op.execute("""
        CREATE OR REPLACE FUNCTION block_audit_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit log is append-only. UPDATE and DELETE are forbidden (NFR-SEC-05).';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION block_audit_mutation();
    """)
    # Block UPDATE and DELETE on intent_log
    op.execute("""
        CREATE TRIGGER intent_log_immutable
        BEFORE UPDATE OR DELETE ON intent_log
        FOR EACH ROW EXECUTE FUNCTION block_audit_mutation();
    """)
    # Block UPDATE and DELETE on tool_call_log
    op.execute("""
        CREATE TRIGGER tool_call_log_immutable
        BEFORE UPDATE OR DELETE ON tool_call_log
        FOR EACH ROW EXECUTE FUNCTION block_audit_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS intent_log_immutable ON intent_log")
    op.execute("DROP TRIGGER IF EXISTS tool_call_log_immutable ON tool_call_log")
    op.execute("DROP FUNCTION IF EXISTS block_audit_mutation()")
