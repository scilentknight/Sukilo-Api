"""Link vendor media columns to catalog.media_asset.

Revision ID: 20261028_0040
Revises: 20261028_0039
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261028_0040"
down_revision: str | Sequence[str] | None = "20261028_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add media foreign keys without removing existing storage keys."""
    op.add_column(
        "vendor_document",
        sa.Column("media_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="vendor",
    )
    op.create_foreign_key(
        "fk_vendor_document_media_id",
        "vendor_document",
        "media_asset",
        ["media_id"],
        ["media_id"],
        source_schema="vendor",
        referent_schema="catalog",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_vendor_store_logo_media_id",
        "store",
        "media_asset",
        ["logo_media_id"],
        ["media_id"],
        source_schema="vendor",
        referent_schema="catalog",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_vendor_store_banner_media_id",
        "store",
        "media_asset",
        ["banner_media_id"],
        ["media_id"],
        source_schema="vendor",
        referent_schema="catalog",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove media foreign keys and the document media column."""
    op.drop_constraint("fk_vendor_store_banner_media_id", "store", schema="vendor", type_="foreignkey")
    op.drop_constraint("fk_vendor_store_logo_media_id", "store", schema="vendor", type_="foreignkey")
    op.drop_constraint(
        "fk_vendor_document_media_id",
        "vendor_document",
        schema="vendor",
        type_="foreignkey",
    )
    op.drop_column("vendor_document", "media_id", schema="vendor")
