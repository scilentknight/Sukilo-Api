"""Create IAM roles.

Revision ID: 20261012_0009
Revises: 20261012_0008
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0009"
down_revision: str | Sequence[str] | None = "20261012_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scoped role definitions."""
    op.create_table(
        "role",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.CheckConstraint(
            "scope_type IN ('GLOBAL', 'COUNTRY', 'VENDOR', 'STORE')",
            name="ck_role_scope_type",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RETIRED')",
            name="ck_role_status",
        ),
        sa.PrimaryKeyConstraint("role_id"),
        schema="iam",
    )
    op.create_index("uq_role_code", "role", ["code"], unique=True, schema="iam")
    op.create_index("ix_role_status", "role", ["status"], schema="iam")


def downgrade() -> None:
    """Drop IAM roles."""
    op.drop_index("ix_role_status", table_name="role", schema="iam")
    op.drop_index("uq_role_code", table_name="role", schema="iam")
    op.drop_table("role", schema="iam")
