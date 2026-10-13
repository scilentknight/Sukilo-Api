"""Define rotating refresh-token session persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserSession(Base):
    """Store one refresh-token generation in a session family."""

    __tablename__ = "user_session"
    __table_args__ = (
        Index(
            "uq_user_session_refresh_token_hash",
            "refresh_token_hash",
            unique=True,
        ),
        Index(
            "ix_user_session_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_user_session_family", "family_id"),
        Index(
            "ix_user_session_expires_active",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        {"schema": "iam"},
    )

    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    family_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(Text(), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(Text(), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_session.session_id", ondelete="RESTRICT"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoke_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return a representation without token or client digests."""
        return (
            f"UserSession(session_id={self.session_id!r}, "
            f"user_id={self.user_id!r})"
        )
