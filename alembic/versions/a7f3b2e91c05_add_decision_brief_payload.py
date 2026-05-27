"""Add decision_brief_payload column and BRIEFING/AWAITING_BRIEF_CONFIRMATION workflow statuses

Revision ID: a7f3b2e91c05
Revises: f1a9c3e72b08
Create Date: 2026-05-27

Changes:
  1. Add BRIEFING to workflowstatus enum
  2. Add AWAITING_BRIEF_CONFIRMATION to workflowstatus enum
     (Postgres requires each ADD VALUE in its own committed transaction)
  3. Add decision_brief_payload JSONB column to workflow_executions
     (stores the DecisionBriefResult between HITL pause 1 and draft_node)
"""

from alembic import op
import sqlalchemy as sa


revision = "a7f3b2e91c05"
down_revision = "f1a9c3e72b08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Step 1: Add BRIEFING enum value ─────────────────────────────────────
    # Postgres requires each ADD VALUE in a committed transaction — they cannot
    # share a transaction with DDL that uses the new value.
    op.execute("ALTER TYPE workflowstatus ADD VALUE IF NOT EXISTS 'BRIEFING'")

    # Commit so Postgres registers the new value before the next ALTER
    op.execute("COMMIT")

    # ── Step 2: Add AWAITING_BRIEF_CONFIRMATION enum value ───────────────────
    op.execute(
        "ALTER TYPE workflowstatus ADD VALUE IF NOT EXISTS 'AWAITING_BRIEF_CONFIRMATION'"
    )

    op.execute("COMMIT")

    # ── Step 3: Add decision_brief_payload column ────────────────────────────
    op.add_column(
        "workflow_executions",
        sa.Column(
            "decision_brief_payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Stores DecisionBriefResult JSONB between HITL pause 1 and draft_node",
        ),
    )


def downgrade() -> None:
    # Remove the column (enum values cannot be removed in Postgres without
    # recreating the type — leave the enum values in place on downgrade)
    op.drop_column("workflow_executions", "decision_brief_payload")
