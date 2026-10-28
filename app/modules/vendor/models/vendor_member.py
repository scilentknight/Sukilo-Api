"""Define membership of one account in one seller organization."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.vendor.types import MemberStatus

_MEMBER_STATUSES = ", ".join(f"'{member.value}'" for member in MemberStatus)


class VendorMember(Base):
    """Link one account to one seller organization."""

    __tablename__ = "vendor_member"
    __table_args__ = (
        UniqueConstraint(
            "vendor_id",
            "user_id",
            name="uq_vendor_member_vendor_user",
        ),
        CheckConstraint(
            f"status IN ({_MEMBER_STATUSES})",
            name="ck_vendor_member_status",
        ),
        CheckConstraint(
            "status <> 'ACTIVE' OR accepted_at IS NOT NULL",
            name="ck_vendor_member_active_requires_accepted_at",
        ),
        {"schema": "vendor"},
    )

    vendor_member_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    vendor_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vendor.vendor.vendor_id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[MemberStatus] = mapped_column(
        Enum(
            MemberStatus,
            name="vendor_member_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
    )
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return a representation that identifies the membership only."""
        return (
            f"VendorMember(vendor_member_id={self.vendor_member_id!r}, "
            f"status={self.status!r})"
        )
