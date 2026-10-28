"""Create the catalog PostgreSQL schema.

Revision ID: 20261028_0031
Revises: 20261028_0030
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0031"
down_revision: str | Sequence[str] | None = "20261028_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the catalog database schema."""
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS catalog"))


def downgrade() -> None:
    """Drop the empty catalog schema."""
    op.execute(sa.text("DROP SCHEMA IF EXISTS catalog"))
