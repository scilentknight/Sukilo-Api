"""Create IAM user sessions.

Revision ID: 20261012_0007
Revises: 20261012_0006
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0007"
down_revision: str | Sequence[str] | None = "20261012_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create rotating refresh-token sessions."""
    op.create_table(
        "user_session",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=True),
        sa.Column("device_name", sa.String(length=160), nullable=True),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column("user_agent_hash", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replaced_by_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_session_id"],
            ["iam.user_session.session_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id"),
        schema="iam",
    )
    op.create_index(
        "uq_user_session_refresh_token_hash",
        "user_session",
        ["refresh_token_hash"],
        unique=True,
        schema="iam",
    )
    op.create_index(
        "ix_user_session_user_active",
        "user_session",
        ["user_id"],
        schema="iam",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_user_session_family",
        "user_session",
        ["family_id"],
        schema="iam",
    )
    op.create_index(
        "ix_user_session_expires_active",
        "user_session",
        ["expires_at"],
        schema="iam",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Drop IAM user sessions."""
    for index_name in (
        "ix_user_session_expires_active",
        "ix_user_session_family",
        "ix_user_session_user_active",
        "uq_user_session_refresh_token_hash",
    ):
        op.drop_index(
            index_name,
            table_name="user_session",
            schema="iam",
        )
    op.drop_table("user_session", schema="iam")
