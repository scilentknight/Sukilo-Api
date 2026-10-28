"""Define shared category tables."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
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
    """Build a varchar enum column for one catalog status."""
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=24,
    )


class Category(Base):
    """Store one shared category node."""

    __tablename__ = "category"
    __table_args__ = (
        UniqueConstraint("code", name="uq_catalog_category_code"),
        CheckConstraint(
            f"status IN ({_RECORD_STATUSES})",
            name="ck_catalog_category_status",
        ),
        {"schema": "catalog"},
    )

    category_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        nullable=True,
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[RecordStatus] = mapped_column(
        _status_column(RecordStatus, "catalog_category_status"),
        nullable=False,
        default=RecordStatus.ACTIVE,
        server_default=RecordStatus.ACTIVE.value,
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
        """Return the category identity and status."""
        return f"Category(category_id={self.category_id!r}, status={self.status!r})"


class CategoryCountry(Base):
    """Store whether one category is enabled in one country."""

    __tablename__ = "category_country"
    __table_args__ = (
        Index("ix_catalog_category_country_country", "country_id", "enabled"),
        {"schema": "catalog"},
    )

    category_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    country_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class CategoryTranslation(Base):
    """Store one locale label for a category."""

    __tablename__ = "category_translation"
    __table_args__ = {"schema": "catalog"}

    category_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    locale_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)


class CategoryChangeRequest(Base):
    """Store a proposal that does not publish a category by itself."""

    __tablename__ = "category_change_request"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_REQUEST_STATUSES})",
            name="ck_catalog_category_request_status",
        ),
        {"schema": "catalog"},
    )

    request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    category_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        nullable=True,
    )
    proposed_parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.category.category_id", ondelete="RESTRICT"),
        nullable=True,
    )
    proposed_code: Mapped[str] = mapped_column(String(80), nullable=False)
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
        _status_column(RequestStatus, "catalog_category_request_status"),
        nullable=False,
        default=RequestStatus.PENDING,
        server_default=RequestStatus.PENDING.value,
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
