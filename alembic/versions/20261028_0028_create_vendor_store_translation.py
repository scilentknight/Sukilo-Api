"""Create localized store copy.

Revision ID: 20261028_0028
Revises: 20261028_0027
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0028"
down_revision: str | Sequence[str] | None = "20261028_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create store translations keyed by store and locale."""
    op.create_table(
        "store_translation",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale_code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("seo_title", sa.String(length=160), nullable=True),
        sa.Column("seo_description", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["vendor.store.store_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("store_id", "locale_code"),
        schema="vendor",
    )


def downgrade() -> None:
    """Drop localized store copy."""
    op.drop_table("store_translation", schema="vendor")
