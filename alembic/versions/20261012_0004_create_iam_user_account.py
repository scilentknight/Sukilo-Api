"""Create the IAM user account table.

Revision ID: 20261012_0004
Revises: 20261012_0003
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0004"
down_revision: str | Sequence[str] | None = "20261012_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the canonical global user account table."""
    op.create_table(
        "user_account",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        sa.Column("phone_e164", sa.String(length=24), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="PENDING", nullable=False),
        sa.Column("auth_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'CLOSED')",
            name="ck_user_account_status",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema="iam",
    )
    op.create_index(
        "uq_user_account_email_present",
        "user_account",
        ["email"],
        unique=True,
        schema="iam",
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "uq_user_account_phone_present",
        "user_account",
        ["phone_e164"],
        unique=True,
        schema="iam",
        postgresql_where=sa.text("phone_e164 IS NOT NULL"),
    )
    op.create_index(
        "ix_user_account_status",
        "user_account",
        ["status"],
        unique=False,
        schema="iam",
    )


def downgrade() -> None:
    """Drop the IAM user account table."""
    op.drop_index(
        "ix_user_account_status",
        table_name="user_account",
        schema="iam",
    )
    op.drop_index(
        "uq_user_account_phone_present",
        table_name="user_account",
        schema="iam",
    )
    op.drop_index(
        "uq_user_account_email_present",
        table_name="user_account",
        schema="iam",
    )
    op.drop_table("user_account", schema="iam")
