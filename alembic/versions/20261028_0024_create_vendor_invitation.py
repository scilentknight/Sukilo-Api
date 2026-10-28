"""Create hashed seller staff invitations.

Revision ID: 20261028_0024
Revises: 20261028_0023
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0024"
down_revision: str | Sequence[str] | None = "20261028_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create staff invitations that store only a token hash."""
    op.create_table(
        "vendor_invitation",
        sa.Column("invitation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("invitation_id"),
        sa.UniqueConstraint("token_hash", name="uq_vendor_invitation_token_hash"),
        schema="vendor",
    )


def downgrade() -> None:
    """Drop hashed seller staff invitations."""
    op.drop_table("vendor_invitation", schema="vendor")
