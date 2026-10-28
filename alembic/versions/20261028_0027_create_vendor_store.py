"""Create one storefront per seller country.

Revision ID: 20261028_0027
Revises: 20261028_0026
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0027"
down_revision: str | Sequence[str] | None = "20261028_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STORE_STATUSES = "'DRAFT', 'ACTIVE', 'SUSPENDED'"


def upgrade() -> None:
    """Create draft storefronts without media foreign keys."""
    op.create_table(
        "store",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="DRAFT", nullable=False),
        sa.Column("rating", sa.Numeric(4, 2), server_default=sa.text("0"), nullable=False),
        sa.Column("rating_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "followers_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("logo_media_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("banner_media_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(f"status IN ({_STORE_STATUSES})", name="ck_vendor_store_status"),
        sa.CheckConstraint("rating >= 0", name="ck_vendor_store_rating"),
        sa.CheckConstraint("rating_count >= 0", name="ck_vendor_store_rating_count"),
        sa.CheckConstraint("followers_count >= 0", name="ck_vendor_store_followers_count"),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.vendor.vendor_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("store_id"),
        sa.UniqueConstraint("vendor_id", "country_id", name="uq_vendor_store_vendor_country"),
        sa.UniqueConstraint("country_id", "slug", name="uq_vendor_store_country_slug"),
        schema="vendor",
    )


def downgrade() -> None:
    """Drop seller storefronts."""
    op.drop_table("store", schema="vendor")
