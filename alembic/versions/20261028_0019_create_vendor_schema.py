"""Create the vendor PostgreSQL schema.

Revision ID: 20261028_0019
Revises: 20261012_0018
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0019"
down_revision: str | Sequence[str] | None = "20261012_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the vendor database schema."""
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS vendor"))


def downgrade() -> None:
    """Drop the empty vendor schema."""
    op.execute(sa.text("DROP SCHEMA IF EXISTS vendor"))
