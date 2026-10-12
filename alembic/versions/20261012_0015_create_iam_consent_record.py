"""Create IAM consent record table.

Revision ID: 20261012_0015
Revises: 20261012_0014
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0015"
down_revision: str | Sequence[str] | None = "20261012_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSENT_TYPES = (
    "'TERMS', 'PRIVACY', 'MARKETING_EMAIL', 'MARKETING_SMS', 'ANALYTICS'"
)
_CONSENT_SOURCES = "'WEB', 'APP', 'ADMIN', 'SYSTEM'"


def upgrade() -> None:
    """Create append-only consent evidence storage."""
    op.create_table(
        "consent_record",
        sa.Column("consent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_type", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            f"consent_type IN ({_CONSENT_TYPES})",
            name="ck_consent_type",
        ),
        sa.CheckConstraint(
            f"source IN ({_CONSENT_SOURCES})",
            name="ck_consent_source",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("consent_id"),
        schema="iam",
    )
    op.create_index(
        "ix_consent_user_country_type_latest",
        "consent_record",
        [
            "user_id",
            "country_id",
            "consent_type",
            sa.text("recorded_at DESC"),
            sa.text("consent_id DESC"),
        ],
        unique=False,
        schema="iam",
    )
    op.create_index(
        "ix_consent_country_type_recorded",
        "consent_record",
        ["country_id", "consent_type", "recorded_at"],
        unique=False,
        schema="iam",
    )


def downgrade() -> None:
    """Drop IAM consent evidence storage."""
    op.drop_index(
        "ix_consent_country_type_recorded",
        table_name="consent_record",
        schema="iam",
    )
    op.drop_index(
        "ix_consent_user_country_type_latest",
        table_name="consent_record",
        schema="iam",
    )
    op.drop_table("consent_record", schema="iam")
