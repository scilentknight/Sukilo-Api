"""Create seller document metadata.

Revision ID: 20261028_0023
Revises: 20261028_0022
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0023"
down_revision: str | Sequence[str] | None = "20261028_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENT_TYPES = "'REGISTRATION', 'TAX', 'OWNER_ID', 'BANK', 'BRAND_AUTH'"
_VERIFICATION_STATUSES = "'PENDING', 'VERIFIED', 'REJECTED'"


def upgrade() -> None:
    """Create seller document metadata without storing file bytes."""
    op.create_table(
        "vendor_document",
        sa.Column("vendor_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_type", sa.String(length=24), nullable=False),
        sa.Column("document_number", sa.String(length=80), nullable=True),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(length=24),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("verified_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"document_type IN ({_DOCUMENT_TYPES})",
            name="ck_vendor_document_type",
        ),
        sa.CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUSES})",
            name="ck_vendor_document_verification_status",
        ),
        sa.CheckConstraint(
            "verification_status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_vendor_document_rejection_reason",
        ),
        sa.CheckConstraint(
            "(verified_by IS NULL) = (verified_at IS NULL)",
            name="ck_vendor_document_verified_pair",
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
        sa.PrimaryKeyConstraint("vendor_document_id"),
        schema="vendor",
    )
    op.create_index(
        "ix_vendor_document_country",
        "vendor_document",
        ["vendor_country_id", "document_type"],
        unique=False,
        schema="vendor",
    )


def downgrade() -> None:
    """Drop seller document metadata."""
    op.drop_index(
        "ix_vendor_document_country",
        table_name="vendor_document",
        schema="vendor",
    )
    op.drop_table("vendor_document", schema="vendor")
