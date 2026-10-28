"""Define one payout destination for a seller country."""

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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import AccountType, VerificationStatus

_ACCOUNT_TYPES = ", ".join(f"'{member.value}'" for member in AccountType)
_VERIFICATION_STATUSES = ", ".join(
    f"'{member.value}'" for member in VerificationStatus
)


class VendorPayoutAccount(Base):
    """Store one masked payout destination and its secret ciphertext."""

    __tablename__ = "vendor_payout_account"
    __table_args__ = (
        CheckConstraint(
            f"account_type IN ({_ACCOUNT_TYPES})",
            name="ck_vendor_payout_account_type",
        ),
        CheckConstraint(
            f"verification_status IN ({_VERIFICATION_STATUSES})",
            name="ck_vendor_payout_verification_status",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_vendor_payout_currency_code",
        ),
        CheckConstraint(
            "(verified_by IS NULL AND verified_at IS NULL) OR "
            "(verified_by IS NOT NULL AND verified_at IS NOT NULL)",
            name="ck_vendor_payout_verified_pair",
        ),
        Index(
            "uq_vendor_payout_default",
            "vendor_country_id",
            "currency_code",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        {"schema": "vendor"},
    )

    vendor_payout_account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    vendor_country_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor_country.vendor_country_id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_type: Mapped[AccountType] = mapped_column(
        Enum(
            AccountType,
            name="vendor_account_type",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=16,
        ),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    account_holder_name: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    account_number_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    account_number_encrypted: Mapped[str] = mapped_column(String(512), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            name="vendor_payout_verification_status",
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
        """Return a representation that omits the account number."""
        return (
            "VendorPayoutAccount("
            f"vendor_payout_account_id={self.vendor_payout_account_id!r}, "
            f"verification_status={self.verification_status!r})"
        )
