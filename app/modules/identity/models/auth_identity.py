"""Define authentication identities linked to global accounts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.identity.types import AuthProvider

if TYPE_CHECKING:
    from app.modules.identity.models.user_account import UserAccount


class AuthIdentity(Base):
    """Link a local or external login identity to one account."""

    __tablename__ = "auth_identity"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_auth_identity_provider_subject",
        ),
        Index("ix_auth_identity_user_id", "user_id"),
        {"schema": "iam"},
    )

    auth_identity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[AuthProvider] = mapped_column(
        Enum(
            AuthProvider,
            name="auth_provider",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda members: [member.value for member in members],
            length=32,
        ),
        nullable=False,
    )
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    user: Mapped[UserAccount] = relationship(back_populates="auth_identities")

    def __repr__(self) -> str:
        """Return a representation that excludes provider subject details."""
        return (
            f"AuthIdentity(auth_identity_id={self.auth_identity_id!r}, "
            f"provider={self.provider!r})"
        )
