"""Define localized copy for one seller storefront."""

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StoreTranslation(Base):
    """Store one locale's name and search copy for a store."""

    __tablename__ = "store_translation"
    __table_args__ = {"schema": "vendor"}

    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.store.store_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    locale_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    seo_title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(String(320), nullable=True)

    def __repr__(self) -> str:
        """Return a representation that omits the store copy."""
        return (
            f"StoreTranslation(store_id={self.store_id!r}, "
            f"locale_code={self.locale_code!r})"
        )
