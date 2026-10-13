"""Create IAM customer data export request table.

Revision ID: 20261012_0017
Revises: 20261012_0016
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0017"
down_revision: str | Sequence[str] | None = "20261012_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'PENDING', 'PROCESSING', 'READY', 'FAILED', 'EXPIRED', 'DOWNLOADED'"


def _nullable_timestamp(name: str) -> sa.Column:
    """Build one nullable timezone-aware timestamp column."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    """Create asynchronous customer data export request storage."""
    op.create_table(
        "data_export_request",
        sa.Column(
            "export_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("country_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("artifact_reference", sa.Text(), nullable=True),
        _nullable_timestamp("artifact_expires_at"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _nullable_timestamp("started_at"),
        _nullable_timestamp("completed_at"),
        _nullable_timestamp("failed_at"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        _nullable_timestamp("downloaded_at"),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})",
            name="ck_data_export_request_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("export_request_id"),
        sa.UniqueConstraint(
            "user_id",
            "country_id",
            "idempotency_key",
            name="uq_data_export_request_user_country_idempotency",
        ),
        schema="iam",
    )
    op.create_index(
        "ix_data_export_request_user_status",
        "data_export_request",
        ["user_id", "status"],
        unique=False,
        schema="iam",
    )
    op.create_index(
        "ix_data_export_request_artifact_expires_at",
        "data_export_request",
        ["artifact_expires_at"],
        unique=False,
        schema="iam",
    )


def downgrade() -> None:
    """Drop customer data export request storage."""
    op.drop_index(
        "ix_data_export_request_artifact_expires_at",
        table_name="data_export_request",
        schema="iam",
    )
    op.drop_index(
        "ix_data_export_request_user_status",
        table_name="data_export_request",
        schema="iam",
    )
    op.drop_table("data_export_request", schema="iam")
