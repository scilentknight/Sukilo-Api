"""Define short-lived OTP challenge persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.identity.types import OtpChannel, OtpPurpose


class OtpChallenge(Base):
    """Store a single-use verification challenge without raw OTP or PII."""

    __tablename__ = "otp_challenge"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_otp_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_otp_max_attempts_positive"),
        Index(
            "ix_otp_challenge_destination_purpose_created",
            "destination_hash",
            "purpose",
            "created_at",
        ),
        Index("ix_otp_challenge_user_created", "user_id", "created_at"),
        Index(
            "ix_otp_challenge_expires_unverified",
            "expires_at",
            postgresql_where=text(
                "verified_at IS NULL AND invalidated_at IS NULL"
            ),
        ),
        {"schema": "iam"},
    )

    otp_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    destination_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    otp_nonce: Mapped[str] = mapped_column(Text(), nullable=False)
    channel: Mapped[OtpChannel] = mapped_column(
        Enum(
            OtpChannel,
            native_enum=False,
            create_constraint=False,
            values_callable=lambda members: [member.value for member in members],
            length=16,
        ),
        nullable=False,
    )
    purpose: Mapped[OtpPurpose] = mapped_column(
        Enum(
            OtpPurpose,
            native_enum=False,
            create_constraint=False,
            values_callable=lambda members: [member.value for member in members],
            length=32,
        ),
        nullable=False,
    )
    otp_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(SmallInteger(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return a representation without OTP or destination digests."""
        return f"OtpChallenge(otp_id={self.otp_id!r}, purpose={self.purpose!r})"
