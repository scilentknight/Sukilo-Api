"""Define customer-owned address persistence."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.customers.types import AddressType

_ADDRESS_TYPES = ", ".join(f"'{member.value}'" for member in AddressType)
_ACTIVE = text("deleted_at IS NULL")
_DEFAULT_SHIPPING = text(
    "is_default_shipping AND deleted_at IS NULL"
)
_DEFAULT_BILLING = text(
    "is_default_billing AND deleted_at IS NULL"
)


class Address(Base):
    """Store one country-scoped customer delivery or billing address."""

    __tablename__ = "address"
    __table_args__ = (
        CheckConstraint(
            f"address_type IN ({_ADDRESS_TYPES})",
            name="ck_address_type",
        ),
        CheckConstraint(
            "is_shipping OR is_billing",
            name="ck_address_shipping_or_billing",
        ),
        CheckConstraint(
            "NOT is_default_shipping OR is_shipping",
            name="ck_address_default_shipping_requires_shipping",
        ),
        CheckConstraint(
            "NOT is_default_billing OR is_billing",
            name="ck_address_default_billing_requires_billing",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_address_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_address_longitude_range",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR (NOT is_default_shipping AND NOT is_default_billing)",
            name="ck_address_deleted_not_default",
        ),
        Index(
            "ix_address_user_country_active",
            "user_id",
            "country_id",
            postgresql_where=_ACTIVE,
        ),
        Index(
            "uq_address_default_shipping_user_country",
            "user_id",
            "country_id",
            unique=True,
            postgresql_where=_DEFAULT_SHIPPING,
        ),
        Index(
            "uq_address_default_billing_user_country",
            "user_id",
            "country_id",
            unique=True,
            postgresql_where=_DEFAULT_BILLING,
        ),
        {"schema": "iam"},
    )

    address_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    country_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    geography_area_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    recipient_name: Mapped[str] = mapped_column(String(160), nullable=False)
    phone_e164: Mapped[str] = mapped_column(String(24), nullable=False)
    address_line_1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7),
        nullable=True,
    )
    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 7),
        nullable=True,
    )
    address_type: Mapped[AddressType] = mapped_column(String(24), nullable=False)
    is_shipping: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    is_billing: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_default_shipping: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_default_billing: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )
