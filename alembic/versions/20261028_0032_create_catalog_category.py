"""Create shared category tables.

Revision ID: 20261028_0032
Revises: 20261028_0031
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0032"
down_revision: str | Sequence[str] | None = "20261028_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECORD_STATUSES = "'ACTIVE', 'RETIRED'"
_REQUEST_STATUSES = "'PENDING', 'APPROVED', 'REJECTED'"


def upgrade() -> None:
    """Create category nodes, translations, country switches, and requests."""
    op.create_table(
        "category",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="ACTIVE", nullable=False),
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
        sa.CheckConstraint(f"status IN ({_RECORD_STATUSES})", name="ck_catalog_category_status"),
        sa.ForeignKeyConstraint(["parent_id"], ["catalog.category.category_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("category_id"),
        sa.UniqueConstraint("code", name="uq_catalog_category_code"),
        schema="catalog",
    )
    op.create_table(
        "category_country",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["catalog.category.category_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("category_id", "country_id"),
        schema="catalog",
    )
    op.create_index(
        "ix_catalog_category_country_country",
        "category_country",
        ["country_id", "enabled"],
        schema="catalog",
    )
    op.create_table(
        "category_translation",
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale_code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["catalog.category.category_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("category_id", "locale_code"),
        schema="catalog",
    )
    op.create_table(
        "category_change_request",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_code", sa.String(length=80), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="PENDING", nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_REQUEST_STATUSES})",
            name="ck_catalog_category_request_status",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["catalog.category.category_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposed_parent_id"],
            ["catalog.category.category_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendor.vendor.vendor_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["iam.user_account.user_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["iam.user_account.user_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("request_id"),
        schema="catalog",
    )


def downgrade() -> None:
    """Drop shared category tables."""
    op.drop_table("category_change_request", schema="catalog")
    op.drop_table("category_translation", schema="catalog")
    op.drop_index(
        "ix_catalog_category_country_country",
        table_name="category_country",
        schema="catalog",
    )
    op.drop_table("category_country", schema="catalog")
    op.drop_table("category", schema="catalog")
