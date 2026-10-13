"""Create the IAM PostgreSQL schema and required extension.

Revision ID: 20261012_0003
Revises: 20261007_0002
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0003"
down_revision: str | Sequence[str] | None = "20261007_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create IAM database prerequisites."""
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS citext"))
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS iam"))


def downgrade() -> None:
    """Drop the empty IAM schema while preserving shared extensions."""
    op.execute(sa.text("DROP SCHEMA IF EXISTS iam"))
