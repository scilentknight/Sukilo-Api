"""Create seller payout destinations.

Revision ID: 20261028_0026
Revises: 20261028_0025
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0026"
down_revision: str | Sequence[str] | None = "20261028_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACCOUNT_TYPES = "'BANK', 'WALLET'"
_VERIFICATION_STATUSES = "'PENDING', 'VERIFIED', 'REJECTED'"


def upgrade() -> None:
    """Create payout destinations with a masked number and ciphertext."""
    op.create_table(
        "vendor_payout_account",
        sa.Column(
            "vendor_payout_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("vendor_country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_type", sa.String(length=16), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("account_holder_name", sa.String(length=200), nullable=False),
        sa.Column("bank_name", sa.String(length=160), nullable=True),
        sa.Column("account_number_masked", sa.String(length=32), nullable=False),
        sa.Column("account_number_encrypted", sa.String(length=512), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "verification_status",
            sa.String(length=24),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("verified_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"account_type IN ({_ACCOUNT_TYPES})",
            name="ck_vendor_payout_account_type",
        ),
        sa.CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUSES})",
            name="ck_vendor_payout_verification_status",
        ),
        sa.CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_vendor_payout_currency_code",
        ),
        sa.CheckConstraint(
            "(verified_by IS NULL AND verified_at IS NULL) OR "
            "(verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_vendor_payout_verified_pair",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_country_id"],
            ["vendor.vendor_country.vendor_country_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("vendor_payout_account_id"),
        schema="vendor",
    )
    op.create_index(
        "uq_vendor_payout_default",
        "vendor_payout_account",
        ["vendor_country_id", "currency_code"],
        unique=True,
        schema="vendor",
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    """Drop seller payout destinations."""
    op.drop_index(
        "uq_vendor_payout_default",
        table_name="vendor_payout_account",
        schema="vendor",
    )
    op.drop_table("vendor_payout_account", schema="vendor")
