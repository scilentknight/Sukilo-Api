"""Create IAM authorization grants.

Revision ID: 20261012_0008
Revises: 20261012_0007
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0008"
down_revision: str | Sequence[str] | None = "20261012_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create purpose-bound single-use authorization grants."""
    op.create_table(
        "auth_grant",
        sa.Column("grant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("action_class", sa.String(length=64), nullable=True),
        sa.Column("subject_hash", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "authentication_methods",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["iam.user_session.session_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("grant_id"),
        schema="iam",
    )
    op.create_index(
        "uq_auth_grant_token_hash",
        "auth_grant",
        ["token_hash"],
        unique=True,
        schema="iam",
    )
    active = sa.text("consumed_at IS NULL AND revoked_at IS NULL")
    op.create_index(
        "ix_auth_grant_user_purpose_active",
        "auth_grant",
        ["user_id", "purpose"],
        schema="iam",
        postgresql_where=active,
    )
    op.create_index(
        "ix_auth_grant_session_active",
        "auth_grant",
        ["session_id"],
        schema="iam",
        postgresql_where=active,
    )
    op.create_index(
        "ix_auth_grant_expires_active",
        "auth_grant",
        ["expires_at"],
        schema="iam",
        postgresql_where=active,
    )


def downgrade() -> None:
    """Drop IAM authorization grants."""
    for index_name in (
        "ix_auth_grant_expires_active",
        "ix_auth_grant_session_active",
        "ix_auth_grant_user_purpose_active",
        "uq_auth_grant_token_hash",
    ):
        op.drop_index(
            index_name,
            table_name="auth_grant",
            schema="iam",
        )
    op.drop_table("auth_grant", schema="iam")
