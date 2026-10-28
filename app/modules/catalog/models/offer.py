"""Define seller offer tables."""

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
from app.modules.catalog.types import OfferStatus, PublicationStatus

_OFFER_STATUSES = ", ".join(f"'{member.value}'" for member in OfferStatus)
_PUBLICATION_STATUSES = ", ".join(f"'{member.value}'" for member in PublicationStatus)


def _status_column(enum_type: type[OfferStatus] | type[PublicationStatus], name: str):
    """Build a varchar enum column for one offer status."""
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=24,
    )


class SellerOffer(Base):
    """Store one seller's commercial offer for one shared SKU."""

    __tablename__ = "seller_offer"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "sku_id",
            "store_id",
            name="uq_catalog_offer_vendor_sku_store",
        ),
        UniqueConstraint("vendor_id", "seller_sku", name="uq_catalog_offer_vendor_seller_sku"),
        CheckConstraint(f"status IN ({_OFFER_STATUSES})", name="ck_catalog_offer_status"),
        {"schema": "catalog"},
    )

    offer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    vendor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor.vendor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    store_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.store.store_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    seller_sku: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[OfferStatus] = mapped_column(
        _status_column(OfferStatus, "catalog_offer_status"),
        nullable=False,
        default=OfferStatus.DRAFT,
        server_default=OfferStatus.DRAFT.value,
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
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


class OfferCountry(Base):
    """Store the price and publication of one offer in one country."""

    __tablename__ = "offer_country"
    __table_args__ = (
        CheckConstraint(
            f"publication_status IN ({_PUBLICATION_STATUSES})",
            name="ck_catalog_offer_country_publication",
        ),
        CheckConstraint("list_price > 0", name="ck_catalog_offer_country_price"),
        CheckConstraint("char_length(currency_code) = 3", name="ck_catalog_offer_country_currency"),
        {"schema": "catalog"},
    )

    offer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.seller_offer.offer_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    country_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    publication_status: Mapped[PublicationStatus] = mapped_column(
        _status_column(PublicationStatus, "catalog_offer_publication_status"),
        nullable=False,
        default=PublicationStatus.DRAFT,
        server_default=PublicationStatus.DRAFT.value,
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    list_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
