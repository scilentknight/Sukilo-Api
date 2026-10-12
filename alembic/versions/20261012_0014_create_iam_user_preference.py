"""Create IAM user preference table.

Revision ID: 20261012_0014
Revises: 20261012_0013
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0014"
down_revision: str | Sequence[str] | None = "20261012_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one-to-one user preference storage."""
    op.create_table(
        "user_preference",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preferred_country_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preferred_locale_code", sa.String(length=12), nullable=True),
        sa.Column("preferred_currency_code", sa.String(length=3), nullable=True),
        sa.Column(
            "marketing_email",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "marketing_sms",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id"),
        schema="iam",
    )


def downgrade() -> None:
    """Drop IAM user preference storage."""
    op.drop_table("user_preference", schema="iam")
