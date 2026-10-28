"""Create the seller membership table.

Revision ID: 20261028_0022
Revises: 20261028_0021
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0022"
down_revision: str | Sequence[str] | None = "20261028_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEMBER_STATUSES = "'INVITED', 'ACTIVE', 'SUSPENDED'"


def upgrade() -> None:
    """Create organization membership rows without seeding any members."""
    op.create_table(
        "vendor_member",
        sa.Column("vendor_member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_MEMBER_STATUSES})",
            name="ck_vendor_member_status",
        ),
        sa.CheckConstraint(
            "status <> 'ACTIVE' OR accepted_at IS NOT NULL",
            name="ck_vendor_member_active_requires_accepted_at",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.vendor.vendor_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("vendor_member_id"),
        sa.UniqueConstraint(
            "vendor_id",
            "user_id",
            name="uq_vendor_member_vendor_user",
        ),
        schema="vendor",
    )


def downgrade() -> None:
    """Drop the seller membership table."""
    op.drop_table("vendor_member", schema="vendor")
