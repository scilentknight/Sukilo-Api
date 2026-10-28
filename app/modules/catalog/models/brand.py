"""Define shared brand tables."""

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
from app.modules.catalog.types import RecordStatus, RequestStatus

_RECORD_STATUSES = ", ".join(f"'{member.value}'" for member in RecordStatus)
_REQUEST_STATUSES = ", ".join(f"'{member.value}'" for member in RequestStatus)


def _status_column(enum_type: type[RecordStatus] | type[RequestStatus], name: str):
    """Build a varchar enum column for one brand status."""
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=24,
    )


class Brand(Base):
    """Store one canonical brand."""

    __tablename__ = "brand"
    __table_args__ = (
        UniqueConstraint("name", name="uq_catalog_brand_name"),
        UniqueConstraint("normalized_name", name="uq_catalog_brand_normalized_name"),
        CheckConstraint(f"status IN ({_RECORD_STATUSES})", name="ck_catalog_brand_status"),
        {"schema": "catalog"},
    )

    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[RecordStatus] = mapped_column(
        _status_column(RecordStatus, "catalog_brand_status"),
        nullable=False,
        default=RecordStatus.ACTIVE,
        server_default=RecordStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return the brand identity and status."""
        return f"Brand(brand_id={self.brand_id!r}, status={self.status!r})"


class BrandCountry(Base):
    """Store whether one brand is enabled in one country."""

    __tablename__ = "brand_country"
    __table_args__ = {"schema": "catalog"}

    brand_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.brand.brand_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    country_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class BrandRequest(Base):
    """Store a seller brand proposal without creating the brand."""

    __tablename__ = "brand_request"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_REQUEST_STATUSES})",
            name="ck_catalog_brand_request_status",
        ),
        {"schema": "catalog"},
    )

    request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    proposed_name: Mapped[str] = mapped_column(String(160), nullable=False)
    country_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    vendor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor.vendor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[RequestStatus] = mapped_column(
        _status_column(RequestStatus, "catalog_brand_request_status"),
        nullable=False,
        default=RequestStatus.PENDING,
        server_default=RequestStatus.PENDING.value,
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    brand_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.brand.brand_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
