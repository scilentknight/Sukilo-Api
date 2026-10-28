"""Define shared product tables."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.catalog.types import ModerationStatus, ProductStatus

_PRODUCT_STATUSES = ", ".join(f"'{member.value}'" for member in ProductStatus)
_MODERATION_STATUSES = ", ".join(f"'{member.value}'" for member in ModerationStatus)
_ONE_VALUE = (
    "(option_id IS NOT NULL AND value_text IS NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NOT NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NULL AND value_number IS NOT NULL)"
)


def _status_column(enum_type: type[ProductStatus] | type[ModerationStatus], name: str):
    """Build a varchar enum column for one product status."""
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=24,
    )


class CatalogProduct(Base):
    """Store one shared product without a seller or a price."""

    __tablename__ = "product"
    __table_args__ = (
        CheckConstraint(f"status IN ({_PRODUCT_STATUSES})", name="ck_catalog_product_status"),
        {"schema": "catalog"},
    )

    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.brand.brand_id", ondelete="RESTRICT"),
        nullable=False,
    )
    category_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ProductStatus] = mapped_column(
        _status_column(ProductStatus, "catalog_product_status"),
        nullable=False,
        default=ProductStatus.DRAFT,
        server_default=ProductStatus.DRAFT.value,
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
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


class ProductCountry(Base):
    """Store country moderation for one shared product."""

    __tablename__ = "product_country"
    __table_args__ = (
        CheckConstraint(
            f"moderation_status IN ({_MODERATION_STATUSES})",
            name="ck_catalog_product_country_moderation",
        ),
        {"schema": "catalog"},
    )

    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.product.product_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    country_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        _status_column(ModerationStatus, "catalog_product_moderation_status"),
        nullable=False,
        default=ModerationStatus.PENDING,
        server_default=ModerationStatus.PENDING.value,
    )


class ProductTranslation(Base):
    """Store one locale name for a shared product."""

    __tablename__ = "product_translation"
    __table_args__ = {"schema": "catalog"}

    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.product.product_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    locale_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)


class ProductAttributeValue(Base):
    """Store one product attribute value."""

    __tablename__ = "product_attribute_value"
    __table_args__ = (
        CheckConstraint(_ONE_VALUE, name="ck_catalog_product_attribute_one_value"),
        {"schema": "catalog"},
    )

    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.product.product_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    attribute_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.attribute_definition.attribute_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    option_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.attribute_option.option_id", ondelete="RESTRICT"),
        nullable=True,
    )
    value_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    value_number: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
