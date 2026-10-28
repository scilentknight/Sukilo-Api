"""Create the seller country application table.

Revision ID: 20261028_0021
Revises: 20261028_0020
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0021"
down_revision: str | Sequence[str] | None = "20261028_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONBOARDING_STATUSES = "'DRAFT', 'SUBMITTED', 'REVIEW', 'APPROVED', 'REJECTED'"
_VERIFICATION_STATUSES = "'PENDING', 'VERIFIED', 'REJECTED'"


def upgrade() -> None:
    """Create one country application row per seller and market."""
    op.create_table(
        "vendor_country",
        sa.Column("vendor_country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "onboarding_status",
            sa.String(length=24),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("business_registration_no", sa.String(length=80), nullable=True),
        sa.Column("tax_registration_no", sa.String(length=80), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=24),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "selling_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "payout_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("terms_version", sa.String(length=64), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspension_reason", sa.String(length=500), nullable=True),
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
            f"onboarding_status IN ({_ONBOARDING_STATUSES})",
            name="ck_vendor_country_onboarding_status",
        ),
        sa.CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUSES})",
            name="ck_vendor_country_verification_status",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.vendor.vendor_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("vendor_country_id"),
        sa.UniqueConstraint(
            "vendor_id",
            "country_id",
            name="uq_vendor_country_vendor_country",
        ),
        schema="vendor",
    )
    op.create_index(
        "ix_vendor_country_country_status",
        "vendor_country",
        ["country_id", "onboarding_status"],
        unique=False,
        schema="vendor",
    )


def downgrade() -> None:
    """Drop the seller country application table."""
    op.drop_index(
        "ix_vendor_country_country_status",
        table_name="vendor_country",
        schema="vendor",
    )
    op.drop_table("vendor_country", schema="vendor")
