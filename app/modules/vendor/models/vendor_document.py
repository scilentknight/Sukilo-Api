"""Define metadata for one seller application file."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import DocumentType, VerificationStatus

_DOCUMENT_TYPES = ", ".join(f"'{member.value}'" for member in DocumentType)
_VERIFICATION_STATUSES = ", ".join(
    f"'{member.value}'" for member in VerificationStatus
)


class VendorDocument(Base):
    """Store metadata for one private seller document."""

    __tablename__ = "vendor_document"
    __table_args__ = (
        CheckConstraint(
            f"document_type IN ({_DOCUMENT_TYPES})",
            name="ck_vendor_document_type",
        ),
        CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUSES})",
            name="ck_vendor_document_verification_status",
        ),
        CheckConstraint(
            "verification_status <> 'REJECTED' OR rejection_reason IS NOT NULL",
            name="ck_vendor_document_rejection_reason",
        ),
        CheckConstraint(
            "(verified_by IS NULL) = (verified_at IS NULL)",
            name="ck_vendor_document_verified_pair",
        ),
        Index("ix_vendor_document_country", "vendor_country_id", "document_type"),
        {"schema": "vendor"},
    )

    vendor_document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    vendor_country_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor_country.vendor_country_id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(
            DocumentType,
            name="vendor_document_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
    )
    document_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    media_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("catalog.media_asset.media_id", ondelete="RESTRICT"),
        nullable=True,
    )
    issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            name="vendor_document_verification_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
        default=VerificationStatus.PENDING,
        server_default=VerificationStatus.PENDING.value,
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return a representation that omits document numbers and storage keys."""
        return (
            f"VendorDocument(vendor_document_id={self.vendor_document_id!r}, "
            f"document_type={self.document_type!r})"
        )
