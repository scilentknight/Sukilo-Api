"""Define the global seller organization model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import VendorStatus, VendorType

_VENDOR_TYPES = ", ".join(f"'{member.value}'" for member in VendorType)
_VENDOR_STATUSES = ", ".join(f"'{member.value}'" for member in VendorStatus)


class Vendor(Base):
    """Store one global seller organization."""

    __tablename__ = "vendor"
    __table_args__ = (
        CheckConstraint(
            f"vendor_type IN ({_VENDOR_TYPES})",
            name="ck_vendor_vendor_type",
        ),
        CheckConstraint(
            f"status IN ({_VENDOR_STATUSES})",
            name="ck_vendor_status",
        ),
        Index("ix_vendor_created_by_user", "created_by_user_id"),
        Index("ix_vendor_status", "status"),
        {"schema": "vendor"},
    )

    vendor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    vendor_type: Mapped[VendorType] = mapped_column(
        Enum(
            VendorType,
            name="vendor_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
    )
    registration_country_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    registration_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tax_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[VendorStatus] = mapped_column(
        Enum(
            VendorStatus,
            name="vendor_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
        default=VendorStatus.PENDING,
        server_default=VendorStatus.PENDING.value,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
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
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    def __repr__(self) -> str:
        """Return a representation that omits registration and tax numbers."""
        return f"Vendor(vendor_id={self.vendor_id!r}, status={self.status!r})"
