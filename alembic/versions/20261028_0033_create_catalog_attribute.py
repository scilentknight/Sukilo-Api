"""Create shared attribute tables.

Revision ID: 20261028_0033
Revises: 20261028_0032
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0033"
down_revision: str | Sequence[str] | None = "20261028_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DATA_TYPES = "'TEXT', 'NUMBER', 'OPTION'"
_RECORD_STATUSES = "'ACTIVE', 'RETIRED'"


def upgrade() -> None:
    """Create attribute definitions, options, translations, and category links."""
    op.create_table(
        "attribute_definition",
        sa.Column("attribute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("data_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="ACTIVE", nullable=False),
        sa.CheckConstraint(f"data_type IN ({_DATA_TYPES})", name="ck_catalog_attribute_data_type"),
        sa.CheckConstraint(f"status IN ({_RECORD_STATUSES})", name="ck_catalog_attribute_status"),
        sa.PrimaryKeyConstraint("attribute_id"),
        sa.UniqueConstraint("code", name="uq_catalog_attribute_code"),
        schema="catalog",
    )
    op.create_table(
        "attribute_option",
        sa.Column("option_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attribute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["catalog.attribute_definition.attribute_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("option_id"),
        sa.UniqueConstraint("attribute_id", "code", name="uq_catalog_attribute_option_code"),
        schema="catalog",
    )
    op.create_table(
        "attribute_translation",
        sa.Column("attribute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale_code", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["catalog.attribute_definition.attribute_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("attribute_id", "locale_code"),
        schema="catalog",
    )
    op.create_table(
        "attribute_option_translation",
        sa.Column("option_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale_code", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["option_id"],
            ["catalog.attribute_option.option_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("option_id", "locale_code"),
        schema="catalog",
    )
    op.create_table(
        "category_attribute",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attribute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["catalog.category.category_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attribute_id"],
            ["catalog.attribute_definition.attribute_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("category_id", "attribute_id"),
        schema="catalog",
    )


def downgrade() -> None:
    """Drop shared attribute tables."""
    op.drop_table("category_attribute", schema="catalog")
    op.drop_table("attribute_option_translation", schema="catalog")
    op.drop_table("attribute_translation", schema="catalog")
    op.drop_table("attribute_option", schema="catalog")
    op.drop_table("attribute_definition", schema="catalog")
