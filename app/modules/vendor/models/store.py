"""Define one seller storefront in one country."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import StorefrontStatus

_STORE_STATUSES = ", ".join(f"'{member.value}'" for member in StorefrontStatus)


class Store(Base):
    """Store the public shop for one seller in one country."""

    __tablename__ = "store"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "country_id",
            name="uq_vendor_store_vendor_country",
        ),
        UniqueConstraint(
            "country_id",
            "slug",
            name="uq_vendor_store_country_slug",
        ),
        CheckConstraint(
            f"status IN ({_STORE_STATUSES})",
            name="ck_vendor_store_status",
        ),
        CheckConstraint("rating >= 0", name="ck_vendor_store_rating"),
        CheckConstraint("rating_count >= 0", name="ck_vendor_store_rating_count"),
        CheckConstraint(
            "followers_count >= 0",
            name="ck_vendor_store_followers_count",
        ),
        {"schema": "vendor"},
    )

    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    vendor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor.vendor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    country_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[StorefrontStatus] = mapped_column(
        Enum(
            StorefrontStatus,
            name="vendor_store_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
        default=StorefrontStatus.DRAFT,
        server_default=StorefrontStatus.DRAFT.value,
    )
    rating: Mapped[Decimal] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        default=Decimal("0"),
        server_default=text("0"),
    )
    rating_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    followers_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    logo_media_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.media_asset.media_id", ondelete="RESTRICT"),
        nullable=True,
    )
    banner_media_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.media_asset.media_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    def __repr__(self) -> str:
        """Return a representation of the store identity and status."""
        return f"Store(store_id={self.store_id!r}, status={self.status!r})"
