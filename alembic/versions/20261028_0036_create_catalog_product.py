"""Create shared product tables.

Revision ID: 20261028_0036
Revises: 20261028_0035
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0036"
down_revision: str | Sequence[str] | None = "20261028_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRODUCT_STATUSES = "'DRAFT', 'ACTIVE', 'RETIRED'"
_MODERATION_STATUSES = "'PENDING', 'APPROVED', 'REJECTED'"
_ONE_VALUE = (
    "(option_id IS NOT NULL AND value_text IS NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NOT NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NULL AND value_number IS NOT NULL)"
)


def upgrade() -> None:
    """Create shared products without a seller or a price."""
    op.create_table(
        "product",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="DRAFT", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"status IN ({_PRODUCT_STATUSES})", name="ck_catalog_product_status"),
        sa.ForeignKeyConstraint(["brand_id"], ["catalog.brand.brand_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["category_id"], ["catalog.category.category_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["iam.user_account.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("product_id"),
        schema="catalog",
    )
    op.create_table(
        "product_country",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("moderation_status", sa.String(length=24), server_default="PENDING", nullable=False),
        sa.CheckConstraint(
            f"moderation_status IN ({_MODERATION_STATUSES})",
            name="ck_catalog_product_country_moderation",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["catalog.product.product_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("product_id", "country_id"),
        schema="catalog",
    )
    op.create_table(
        "product_translation",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale_code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["catalog.product.product_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("product_id", "locale_code"),
        schema="catalog",
    )
    op.create_table(
        "product_attribute_value",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attribute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("option_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Numeric(18, 4), nullable=True),
        sa.CheckConstraint(_ONE_VALUE, name="ck_catalog_product_attribute_one_value"),
        sa.ForeignKeyConstraint(["product_id"], ["catalog.product.product_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["catalog.attribute_definition.attribute_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["option_id"], ["catalog.attribute_option.option_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("product_id", "attribute_id"),
        schema="catalog",
    )


def downgrade() -> None:
    """Drop shared product tables."""
    op.drop_table("product_attribute_value", schema="catalog")
    op.drop_table("product_translation", schema="catalog")
    op.drop_table("product_country", schema="catalog")
    op.drop_table("product", schema="catalog")
