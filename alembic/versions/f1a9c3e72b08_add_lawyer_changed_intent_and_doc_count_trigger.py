"""Add lawyer_changed_intent to intent_log + document_count trigger

Revision ID: f1a9c3e72b08
Revises: c3794205ec82
Create Date: 2026-05-19

Changes:
  1. Add lawyer_changed_intent boolean column to intent_log
     (FR-AMB-01 research signal)
  2. Add document_count_increment trigger to documents table
     (keeps workspace.document_count accurate per SRS §7.1)
"""

from alembic import op
import sqlalchemy as sa

revision = "f1a9c3e72b08"
down_revision = "c3794205ec82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add lawyer_changed_intent to intent_log
    op.add_column(
        "intent_log",
        sa.Column(
            "lawyer_changed_intent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="True when the lawyer overrode the classifier's primary intent at the ambiguity gate",
        ),
    )

    # 2. Create trigger function to maintain workspace.document_count
    # Uses a AFTER INSERT/DELETE trigger on documents table.
    op.execute("""
        CREATE OR REPLACE FUNCTION maintain_workspace_document_count()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                UPDATE workspaces
                SET document_count = document_count + 1
                WHERE id = NEW.workspace_id;
            ELSIF TG_OP = 'DELETE' THEN
                UPDATE workspaces
                SET document_count = GREATEST(document_count - 1, 0)
                WHERE id = OLD.workspace_id;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_workspace_document_count ON documents;
        CREATE TRIGGER trg_workspace_document_count
        AFTER INSERT OR DELETE ON documents
        FOR EACH ROW
        EXECUTE FUNCTION maintain_workspace_document_count();
    """)

    # 3. Backfill document_count for existing workspaces
    op.execute("""
        UPDATE workspaces w
        SET document_count = (
            SELECT COUNT(*) FROM documents d
            WHERE d.workspace_id = w.id
              AND d.status != 'FAILED'
        );
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_workspace_document_count ON documents;")
    op.execute("DROP FUNCTION IF EXISTS maintain_workspace_document_count();")
    op.drop_column("intent_log", "lawyer_changed_intent")
