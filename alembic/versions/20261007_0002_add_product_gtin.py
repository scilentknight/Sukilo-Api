"""Add unique GTIN column on products.

Revision ID: 20261007_0002
Revises: 20260307_0001
Create Date: 2026-10-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.utils.gs1 import gtin_from_product_id

revision: str = "20261007_0002"
down_revision: Union[str, Sequence[str], None] = "20260307_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add gtin and backfill demo GTINs from product ids."""
    op.add_column("products", sa.Column("gtin", sa.String(length=14), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id FROM products ORDER BY id")).fetchall()
    for (product_id,) in rows:
        connection.execute(
            sa.text("UPDATE products SET gtin = :gtin WHERE id = :product_id"),
            {"gtin": gtin_from_product_id(int(product_id)), "product_id": product_id},
        )
    op.create_index(op.f("ix_products_gtin"), "products", ["gtin"], unique=True)


def downgrade() -> None:
    """Remove the GTIN column."""
    op.drop_index(op.f("ix_products_gtin"), table_name="products")
    op.drop_column("products", "gtin")
