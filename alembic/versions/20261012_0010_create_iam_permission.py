"""Create IAM permissions.

Revision ID: 20261012_0010
Revises: 20261012_0009
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0010"
down_revision: str | Sequence[str] | None = "20261012_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create immutable permission definitions."""
    op.create_table(
        "permission",
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'RETIRED')",
            name="ck_permission_status",
        ),
        sa.CheckConstraint(
            "code ~ '^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){1,2}$'",
            name="ck_permission_code_format",
        ),
        sa.PrimaryKeyConstraint("permission_id"),
        schema="iam",
    )
    op.create_index(
        "uq_permission_code",
        "permission",
        ["code"],
        unique=True,
        schema="iam",
    )
    op.create_index(
        "ix_permission_status",
        "permission",
        ["status"],
        schema="iam",
    )


def downgrade() -> None:
    """Drop IAM permissions."""
    op.drop_index("ix_permission_status", table_name="permission", schema="iam")
    op.drop_index("uq_permission_code", table_name="permission", schema="iam")
    op.drop_table("permission", schema="iam")
