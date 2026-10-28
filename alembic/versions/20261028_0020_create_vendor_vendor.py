"""Create the global seller organization table.

Revision ID: 20261028_0020
Revises: 20261028_0019
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0020"
down_revision: str | Sequence[str] | None = "20261028_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VENDOR_TYPES = "'COMPANY', 'INDIVIDUAL', 'SOLE_TRADER'"
_VENDOR_STATUSES = "'PENDING', 'ACTIVE', 'SUSPENDED', 'CLOSED'"


def upgrade() -> None:
    """Create the global seller organization table."""
    op.create_table(
        "vendor",
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("vendor_type", sa.String(length=24), nullable=False),
        sa.Column(
            "registration_country_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("registration_number", sa.String(length=80), nullable=True),
        sa.Column("tax_number", sa.String(length=80), nullable=True),
        sa.Column("website_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.CheckConstraint(
            f"vendor_type IN ({_VENDOR_TYPES})",
            name="ck_vendor_vendor_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_VENDOR_STATUSES})",
            name="ck_vendor_status",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("vendor_id"),
        schema="vendor",
    )
    op.create_index(
        "ix_vendor_created_by_user",
        "vendor",
        ["created_by_user_id"],
        unique=False,
        schema="vendor",
    )
    op.create_index(
        "ix_vendor_status",
        "vendor",
        ["status"],
        unique=False,
        schema="vendor",
    )


def downgrade() -> None:
    """Drop the global seller organization table."""
    op.drop_index("ix_vendor_status", table_name="vendor", schema="vendor")
    op.drop_index("ix_vendor_created_by_user", table_name="vendor", schema="vendor")
    op.drop_table("vendor", schema="vendor")
