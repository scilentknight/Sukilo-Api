"""Create IAM customer address table.

Revision ID: 20261012_0016
Revises: 20261012_0015
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0016"
down_revision: str | Sequence[str] | None = "20261012_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADDRESS_TYPES = "'HOME', 'OFFICE', 'OTHER'"
_ACTIVE = sa.text("deleted_at IS NULL")
_DEFAULT_SHIPPING = sa.text("is_default_shipping AND deleted_at IS NULL")
_DEFAULT_BILLING = sa.text("is_default_billing AND deleted_at IS NULL")


def upgrade() -> None:
    """Create country-scoped customer address storage."""
    op.create_table(
        "address",
        sa.Column("address_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("geography_area_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_name", sa.String(length=160), nullable=False),
        sa.Column("phone_e164", sa.String(length=24), nullable=False),
        sa.Column("address_line_1", sa.String(length=255), nullable=False),
        sa.Column("address_line_2", sa.String(length=255), nullable=True),
        sa.Column("landmark", sa.String(length=255), nullable=True),
        sa.Column("city_text", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=32), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("address_type", sa.String(length=24), nullable=False),
        sa.Column(
            "is_shipping",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_billing",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_default_shipping",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_default_billing",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"address_type IN ({_ADDRESS_TYPES})",
            name="ck_address_type",
        ),
        sa.CheckConstraint(
            "is_shipping OR is_billing",
            name="ck_address_shipping_or_billing",
        ),
        sa.CheckConstraint(
            "NOT is_default_shipping OR is_shipping",
            name="ck_address_default_shipping_requires_shipping",
        ),
        sa.CheckConstraint(
            "NOT is_default_billing OR is_billing",
            name="ck_address_default_billing_requires_billing",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_address_latitude_range",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_address_longitude_range",
        ),
        sa.CheckConstraint(
            "deleted_at IS NULL OR (NOT is_default_shipping AND NOT is_default_billing)",
            name="ck_address_deleted_not_default",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("address_id"),
        schema="iam",
    )
    op.create_index(
        "ix_address_user_country_active",
        "address",
        ["user_id", "country_id"],
        unique=False,
        schema="iam",
        postgresql_where=_ACTIVE,
    )
    op.create_index(
        "uq_address_default_shipping_user_country",
        "address",
        ["user_id", "country_id"],
        unique=True,
        schema="iam",
        postgresql_where=_DEFAULT_SHIPPING,
    )
    op.create_index(
        "uq_address_default_billing_user_country",
        "address",
        ["user_id", "country_id"],
        unique=True,
        schema="iam",
        postgresql_where=_DEFAULT_BILLING,
    )


def downgrade() -> None:
    """Drop IAM customer address storage."""
    for index_name in (
        "uq_address_default_billing_user_country",
        "uq_address_default_shipping_user_country",
        "ix_address_user_country_active",
    ):
        op.drop_index(
            index_name,
            table_name="address",
            schema="iam",
        )
    op.drop_table("address", schema="iam")
