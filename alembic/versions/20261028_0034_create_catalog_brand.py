"""Create shared brand tables.

Revision ID: 20261028_0034
Revises: 20261028_0033
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0034"
down_revision: str | Sequence[str] | None = "20261028_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORD_STATUSES = "'ACTIVE', 'RETIRED'"
_REQUEST_STATUSES = "'PENDING', 'APPROVED', 'REJECTED'"


def upgrade() -> None:
    """Create brands, country switches, and seller brand requests."""
    op.create_table(
        "brand",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="ACTIVE", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"status IN ({_RECORD_STATUSES})", name="ck_catalog_brand_status"),
        sa.PrimaryKeyConstraint("brand_id"),
        sa.UniqueConstraint("name", name="uq_catalog_brand_name"),
        sa.UniqueConstraint("normalized_name", name="uq_catalog_brand_normalized_name"),
        schema="catalog",
    )
    op.create_table(
        "brand_country",
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["catalog.brand.brand_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("brand_id", "country_id"),
        schema="catalog",
    )
    op.create_table(
        "brand_request",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_name", sa.String(length=160), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="PENDING", nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_REQUEST_STATUSES})",
            name="ck_catalog_brand_request_status",
        ),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendor.vendor.vendor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["iam.user_account.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["iam.user_account.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["brand_id"], ["catalog.brand.brand_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("request_id"),
        schema="catalog",
    )


def downgrade() -> None:
    """Drop shared brand tables."""
    op.drop_table("brand_request", schema="catalog")
    op.drop_table("brand_country", schema="catalog")
    op.drop_table("brand", schema="catalog")
