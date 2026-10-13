"""Create the IAM authentication identity table.

Revision ID: 20261012_0005
Revises: 20261012_0004
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0005"
down_revision: str | Sequence[str] | None = "20261012_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create authentication identities for global accounts."""
    op.create_table(
        "auth_identity",
        sa.Column(
            "auth_identity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("auth_identity_id"),
        sa.UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_auth_identity_provider_subject",
        ),
        schema="iam",
    )
    op.create_index(
        "ix_auth_identity_user_id",
        "auth_identity",
        ["user_id"],
        unique=False,
        schema="iam",
    )


def downgrade() -> None:
    """Drop the IAM authentication identity table."""
    op.drop_index(
        "ix_auth_identity_user_id",
        table_name="auth_identity",
        schema="iam",
    )
    op.drop_table("auth_identity", schema="iam")
