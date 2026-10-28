"""Define one country application for a seller organization."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import OnboardingStatus, VerificationStatus

_ONBOARDING_STATUSES = ", ".join(
    f"'{member.value}'" for member in OnboardingStatus
)
_VERIFICATION_STATUSES = ", ".join(
    f"'{member.value}'" for member in VerificationStatus
)


class VendorCountry(Base):
    """Store one selling application for one country."""

    __tablename__ = "vendor_country"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "country_id",
            name="uq_vendor_country_vendor_country",
        ),
        CheckConstraint(
            f"onboarding_status IN ({_ONBOARDING_STATUSES})",
            name="ck_vendor_country_onboarding_status",
        ),
        CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUSES})",
            name="ck_vendor_country_verification_status",
        ),
        Index("ix_vendor_country_country_status", "country_id", "onboarding_status"),
        {"schema": "vendor"},
    )

    vendor_country_id: Mapped[UUID] = mapped_column(
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
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        Enum(
            OnboardingStatus,
            name="vendor_onboarding_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
        default=OnboardingStatus.DRAFT,
        server_default=OnboardingStatus.DRAFT.value,
    )
    business_registration_no: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    tax_registration_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            name="vendor_verification_status",
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
    selling_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    payout_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    terms_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    suspension_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
        """Return a representation that omits country registration numbers."""
        return (
            f"VendorCountry(vendor_country_id={self.vendor_country_id!r}, "
            f"onboarding_status={self.onboarding_status!r})"
        )
