"""Multi-org UUID migration with memberships.

Revision ID: 20240914_multi_org
Revises: 7fd982d46e27
Create Date: 2024-09-14

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20240914_multi_org"
down_revision = "7fd982d46e27"
branch_labels = None
depends_on = None


UserRoleEnum = postgresql.ENUM("ADMIN", "MEMBER", name="userrole", create_type=False)
FileStatusEnum = postgresql.ENUM(
    "PENDING", "PROCESSING", "READY", "FAILED", name="filestatus", create_type=False
)


def upgrade() -> None:
    # Ensure pgcrypto is available for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 1) organizations: add slug + new UUID id
    op.add_column(
        "organizations", sa.Column("slug", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "organizations",
        sa.Column(
            "id_uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    # ensure temporary uniqueness on id_uuid for FK creation before PK swap
    op.create_unique_constraint(
        "uq_organizations_id_uuid", "organizations", ["id_uuid"]
    )

    # backfill slug from previous id (which held the human slug)
    op.execute("UPDATE organizations SET slug = id")
    op.alter_column("organizations", "slug", nullable=False)
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])

    # 2) Add new org_id columns (UUID) to child tables and backfill via slug→UUID map
    tables = ["users", "files", "api_keys", "invites"]
    for tbl in tables:
        op.add_column(
            tbl,
            sa.Column("org_id_new", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.execute(
            f"""
            UPDATE {tbl} t
            SET org_id_new = o.id_uuid
            FROM organizations o
            WHERE o.slug = t.org_id
            """
        )

    # 3) users: add personal_org_id (UUID) and backfill from current org
    op.add_column(
        "users",
        sa.Column(
            "personal_org_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE users u
        SET personal_org_id = o.id_uuid
        FROM organizations o
        WHERE o.slug = u.org_id
        """
    )

    # 4) user_org_memberships table
    op.create_table(
        "user_org_memberships",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", UserRoleEnum, nullable=False, server_default="MEMBER"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("user_id", "org_id", name="pk_user_org_memberships"),
    )

    # 5) Backfill memberships from existing users (after UUID mapping)
    op.execute(
        """
        INSERT INTO user_org_memberships (user_id, org_id, role, created_at)
        SELECT u.id, o.id_uuid, u.role, COALESCE(u.created_at, CURRENT_TIMESTAMP)
        FROM users u
        JOIN organizations o ON o.slug = u.org_id
        ON CONFLICT DO NOTHING
        """
    )

    # 6) Drop old FKs, replace org_id columns with UUID versions in child tables
    fk_map = {
        "users": "users_org_id_fkey",
        "files": "files_org_id_fkey",
        "api_keys": "api_keys_org_id_fkey",
        "invites": "invites_org_id_fkey",
    }
    for tbl in tables:
        fk_name = fk_map[tbl]
        op.drop_constraint(fk_name, tbl, type_="foreignkey")

        op.drop_column(tbl, "org_id")
        op.alter_column(tbl, "org_id_new", new_column_name="org_id", nullable=False)
        op.create_foreign_key(
            f"{tbl}_org_id_fkey",
            tbl,
            "organizations",
            ["org_id"],
            ["id_uuid"],
            ondelete="CASCADE",
        )
        op.create_index(f"ix_{tbl}_org_id", tbl, ["org_id"])

    # 7) Finalize organizations: swap primary key to UUID, drop old id
    op.drop_constraint("organizations_pkey", "organizations", type_="primary")
    op.drop_column("organizations", "id")
    op.alter_column("organizations", "id_uuid", new_column_name="id", nullable=False)
    op.create_primary_key("organizations_pkey", "organizations", ["id"])


def downgrade() -> None:
    # Given the data-destructive nature and PK/UUID swap, safe downgrade is not provided.
    raise NotImplementedError("Downgrade not supported for multi-org UUID migration")
