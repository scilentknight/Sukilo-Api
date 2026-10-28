"""Create catalog media metadata.

Revision ID: 20261028_0038
Revises: 20261028_0037
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0038"
down_revision: str | Sequence[str] | None = "20261028_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create media metadata and product media order."""
    op.create_table(
        "media_asset",
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendor.vendor.vendor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["iam.user_account.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("media_id"),
        schema="catalog",
    )
    op.create_table(
        "product_media",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["catalog.product.product_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["media_id"], ["catalog.media_asset.media_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("product_id", "media_id"),
        schema="catalog",
    )
    op.create_index(
        "uq_catalog_product_media_primary",
        "product_media",
        ["product_id"],
        unique=True,
        schema="catalog",
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    """Drop catalog media metadata."""
    op.drop_index("uq_catalog_product_media_primary", table_name="product_media", schema="catalog")
    op.drop_table("product_media", schema="catalog")
    op.drop_table("media_asset", schema="catalog")
