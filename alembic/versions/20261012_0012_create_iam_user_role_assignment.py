"""Create IAM scoped user-role assignments.

Revision ID: 20261012_0012
Revises: 20261012_0011
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0012"
down_revision: str | Sequence[str] | None = "20261012_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scoped, revocable role assignments."""
    op.create_table(
        "user_role_assignment",
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from",
            name="ck_assignment_validity_window",
        ),
        sa.CheckConstraint(
            "(scope_type = 'GLOBAL' AND scope_id IS NULL "
            "AND country_id IS NULL AND vendor_id IS NULL) OR "
            "(scope_type = 'COUNTRY' AND scope_id = country_id "
            "AND country_id IS NOT NULL AND vendor_id IS NULL) OR "
            "(scope_type = 'VENDOR' AND scope_id = vendor_id "
            "AND country_id IS NOT NULL AND vendor_id IS NOT NULL) OR "
            "(scope_type = 'STORE' AND scope_id IS NOT NULL "
            "AND country_id IS NOT NULL AND vendor_id IS NOT NULL)",
            name="ck_assignment_scope_shape",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["iam.role.role_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("assignment_id"),
        schema="iam",
    )
    active = sa.text("revoked_at IS NULL")
    op.create_index(
        "ix_assignment_user_active",
        "user_role_assignment",
        ["user_id"],
        schema="iam",
        postgresql_where=active,
    )
    op.create_index(
        "ix_assignment_country_user_active",
        "user_role_assignment",
        ["country_id", "user_id"],
        schema="iam",
        postgresql_where=active,
    )
    op.create_index(
        "ix_assignment_vendor_user_active",
        "user_role_assignment",
        ["vendor_id", "user_id"],
        schema="iam",
        postgresql_where=active,
    )
    op.create_index(
        "ix_assignment_role_scope_active",
        "user_role_assignment",
        ["role_id", "scope_type", "scope_id"],
        schema="iam",
        postgresql_where=active,
    )


def downgrade() -> None:
    """Drop IAM scoped user-role assignments."""
    for index_name in (
        "ix_assignment_role_scope_active",
        "ix_assignment_vendor_user_active",
        "ix_assignment_country_user_active",
        "ix_assignment_user_active",
    ):
        op.drop_index(
            index_name,
            table_name="user_role_assignment",
            schema="iam",
        )
    op.drop_table("user_role_assignment", schema="iam")
