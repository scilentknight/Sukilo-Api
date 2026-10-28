"""Create shared SKU tables.

Revision ID: 20261028_0037
Revises: 20261028_0036
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0037"
down_revision: str | Sequence[str] | None = "20261028_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ONE_VALUE = (
    "(option_id IS NOT NULL AND value_text IS NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NOT NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NULL AND value_number IS NOT NULL)"
)


def upgrade() -> None:
    """Create SKUs that belong to one shared product."""
    op.create_table(
        "sku",
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_code", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["catalog.product.product_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("sku_id"),
        sa.UniqueConstraint("product_id", "sku_code", name="uq_catalog_sku_product_code"),
        schema="catalog",
    )
    op.create_table(
        "sku_attribute_value",
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attribute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Numeric(18, 4), nullable=True),
        sa.CheckConstraint(_ONE_VALUE, name="ck_catalog_sku_attribute_one_value"),
        sa.ForeignKeyConstraint(["sku_id"], ["catalog.sku.sku_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["catalog.attribute_definition.attribute_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["option_id"], ["catalog.attribute_option.option_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("sku_id", "attribute_id"),
        schema="catalog",
    )


def downgrade() -> None:
    """Drop shared SKU tables."""
    op.drop_table("sku_attribute_value", schema="catalog")
    op.drop_table("sku", schema="catalog")
