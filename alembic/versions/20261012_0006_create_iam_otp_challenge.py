"""Create IAM OTP challenges.

Revision ID: 20261012_0006
Revises: 20261012_0005
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0006"
down_revision: str | Sequence[str] | None = "20261012_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create short-lived OTP challenge persistence."""
    op.create_table(
        "otp_challenge",
        sa.Column("otp_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("destination_hash", sa.Text(), nullable=False),
        sa.Column("otp_nonce", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("otp_hash", sa.Text(), nullable=False),
        sa.Column(
            "attempt_count",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("max_attempts", sa.SmallInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_otp_attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_otp_max_attempts_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["iam.user_account.user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("otp_id"),
        schema="iam",
    )
    op.create_index(
        "ix_otp_challenge_destination_purpose_created",
        "otp_challenge",
        ["destination_hash", "purpose", "created_at"],
        schema="iam",
    )
    op.create_index(
        "ix_otp_challenge_user_created",
        "otp_challenge",
        ["user_id", "created_at"],
        schema="iam",
    )
    op.create_index(
        "ix_otp_challenge_expires_unverified",
        "otp_challenge",
        ["expires_at"],
        schema="iam",
        postgresql_where=sa.text(
            "verified_at IS NULL AND invalidated_at IS NULL"
        ),
    )


def downgrade() -> None:
    """Drop IAM OTP challenges."""
    op.drop_index(
        "ix_otp_challenge_expires_unverified",
        table_name="otp_challenge",
        schema="iam",
    )
    op.drop_index(
        "ix_otp_challenge_user_created",
        table_name="otp_challenge",
        schema="iam",
    )
    op.drop_index(
        "ix_otp_challenge_destination_purpose_created",
        table_name="otp_challenge",
        schema="iam",
    )
    op.drop_table("otp_challenge", schema="iam")
