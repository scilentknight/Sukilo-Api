"""Create seller offer tables.

Revision ID: 20261028_0039
Revises: 20261028_0038
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0039"
down_revision: str | Sequence[str] | None = "20261028_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OFFER_STATUSES = "'DRAFT', 'ACTIVE', 'SUSPENDED'"
_PUBLICATION_STATUSES = "'DRAFT', 'PUBLISHED'"


def upgrade() -> None:
    """Create seller offers without a stock count."""
    op.create_table(
        "seller_offer",
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_sku", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="DRAFT", nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"status IN ({_OFFER_STATUSES})", name="ck_catalog_offer_status"),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendor.vendor.vendor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["vendor.store.store_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sku_id"], ["catalog.sku.sku_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("offer_id"),
        sa.UniqueConstraint("vendor_id", "sku_id", "store_id", name="uq_catalog_offer_vendor_sku_store"),
        sa.UniqueConstraint("vendor_id", "seller_sku", name="uq_catalog_offer_vendor_seller_sku"),
        schema="catalog",
    )
    op.create_table(
        "offer_country",
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_status", sa.String(length=24), server_default="DRAFT", nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("list_price", sa.Numeric(14, 2), nullable=False),
        sa.CheckConstraint(
            f"publication_status IN ({_PUBLICATION_STATUSES})",
            name="ck_catalog_offer_country_publication",
        ),
        sa.CheckConstraint("list_price > 0", name="ck_catalog_offer_country_price"),
        sa.CheckConstraint("char_length(currency_code) = 3", name="ck_catalog_offer_country_currency"),
        sa.ForeignKeyConstraint(["offer_id"], ["catalog.seller_offer.offer_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("offer_id", "country_id"),
        schema="catalog",
    )


def downgrade() -> None:
    """Drop seller offer tables."""
    op.drop_table("offer_country", schema="catalog")
    op.drop_table("seller_offer", schema="catalog")
