"""Define the canonical global user account model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.identity.types import AccountStatus

if TYPE_CHECKING:
    from app.modules.identity.models.auth_identity import AuthIdentity


class UserAccount(Base):
    """Store one global identity for a marketplace user."""

    __tablename__ = "user_account"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'CLOSED')",
            name="ck_user_account_status",
        ),
        Index(
            "uq_user_account_email_present",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "uq_user_account_phone_present",
            "phone_e164",
            unique=True,
            postgresql_where=text("phone_e164 IS NOT NULL"),
        ),
        Index("ix_user_account_status", "status"),
        {"schema": "iam"},
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    email: Mapped[str | None] = mapped_column(CITEXT(), nullable=True)
    phone_e164: Mapped[str | None] = mapped_column(String(24), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text(), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(
            AccountStatus,
            name="account_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=24,
        ),
        nullable=False,
        default=AccountStatus.PENDING,
        server_default=AccountStatus.PENDING.value,
    )
    auth_version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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
    auth_identities: Mapped[list[AuthIdentity]] = relationship(
        back_populates="user",
        cascade="save-update, merge",
    )

    def __repr__(self) -> str:
        """Return a representation that excludes credentials and PII."""
        return (
            f"UserAccount(user_id={self.user_id!r}, "
            f"status={self.status!r})"
        )
