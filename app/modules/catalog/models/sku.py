"""Define shared SKU tables."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

_ONE_VALUE = (
    "(option_id IS NOT NULL AND value_text IS NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NOT NULL AND value_number IS NULL) OR "
    "(option_id IS NULL AND value_text IS NULL AND value_number IS NOT NULL)"
)


class Sku(Base):
    """Store one sellable variant of a shared product."""

    __tablename__ = "sku"
    __table_args__ = (
        UniqueConstraint("product_id", "sku_code", name="uq_catalog_sku_product_code"),
        {"schema": "catalog"},
    )

    sku_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.product.product_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_code: Mapped[str] = mapped_column(String(80), nullable=False)


class SkuAttributeValue(Base):
    """Store one variant attribute value."""

    __tablename__ = "sku_attribute_value"
    __table_args__ = (
        CheckConstraint(_ONE_VALUE, name="ck_catalog_sku_attribute_one_value"),
        {"schema": "catalog"},
    )

    sku_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.sku.sku_id", ondelete="RESTRICT"),
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
