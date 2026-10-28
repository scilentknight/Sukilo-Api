"""Define one seller fulfillment location."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import StorefrontStatus

_WAREHOUSE_STATUSES = ", ".join(
    f"'{member.value}'" for member in StorefrontStatus
)


class Warehouse(Base):
    """Store one fulfillment location owned by the seller."""

    __tablename__ = "warehouse"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "code",
            name="uq_vendor_warehouse_vendor_code",
        ),
        CheckConstraint(
            f"status IN ({_WAREHOUSE_STATUSES})",
            name="ck_vendor_warehouse_status",
        ),
        {"schema": "vendor"},
    )

    warehouse_id: Mapped[UUID] = mapped_column(
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
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    city_text: Mapped[str] = mapped_column(String(120), nullable=False)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_name: Mapped[str] = mapped_column(String(160), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    fulfillment_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    pickup_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    status: Mapped[StorefrontStatus] = mapped_column(
        Enum(
            StorefrontStatus,
            name="vendor_warehouse_status",
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

    def __repr__(self) -> str:
        """Return a representation of the warehouse identity and status."""
        return (
            f"Warehouse(warehouse_id={self.warehouse_id!r}, "
            f"status={self.status!r})"
        )
