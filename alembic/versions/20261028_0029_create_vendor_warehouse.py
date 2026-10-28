"""Create seller warehouses with their own address fields.

Revision ID: 20261028_0029
Revises: 20261028_0028
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0029"
down_revision: str | Sequence[str] | None = "20261028_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WAREHOUSE_STATUSES = "'DRAFT', 'ACTIVE', 'SUSPENDED'"


def upgrade() -> None:
    """Create warehouses without a shopper address foreign key."""
    op.create_table(
        "warehouse",
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("address_line_1", sa.String(length=200), nullable=False),
        sa.Column("city_text", sa.String(length=120), nullable=False),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("contact_name", sa.String(length=160), nullable=False),
        sa.Column("contact_phone", sa.String(length=32), nullable=False),
        sa.Column(
            "fulfillment_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "pickup_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), server_default="DRAFT", nullable=False),
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
        sa.CheckConstraint(
            f"status IN ({_WAREHOUSE_STATUSES})",
            name="ck_vendor_warehouse_status",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.vendor.vendor_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("warehouse_id"),
        sa.UniqueConstraint("vendor_id", "code", name="uq_vendor_warehouse_vendor_code"),
        schema="vendor",
    )


def downgrade() -> None:
    """Drop seller warehouses."""
    op.drop_table("warehouse", schema="vendor")
