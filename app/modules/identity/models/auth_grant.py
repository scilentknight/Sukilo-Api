"""Define single-use authorization grant persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.identity.types import GrantPurpose


class AuthGrant(Base):
    """Store a purpose-bound authorization grant without its raw token."""

    __tablename__ = "auth_grant"
    __table_args__ = (
        Index("uq_auth_grant_token_hash", "token_hash", unique=True),
        Index(
            "ix_auth_grant_user_purpose_active",
            "user_id",
            "purpose",
            postgresql_where=text(
                "consumed_at IS NULL AND revoked_at IS NULL"
            ),
        ),
        Index(
            "ix_auth_grant_session_active",
            "session_id",
            postgresql_where=text(
                "consumed_at IS NULL AND revoked_at IS NULL"
            ),
        ),
        Index(
            "ix_auth_grant_expires_active",
            "expires_at",
            postgresql_where=text(
                "consumed_at IS NULL AND revoked_at IS NULL"
            ),
        ),
        {"schema": "iam"},
    )

    grant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_session.session_id", ondelete="RESTRICT"),
        nullable=True,
    )
    purpose: Mapped[GrantPurpose] = mapped_column(
        Enum(
            GrantPurpose,
            native_enum=False,
            create_constraint=False,
            values_callable=lambda members: [member.value for member in members],
            length=32,
        ),
        nullable=False,
    )
    action_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_hash: Mapped[str | None] = mapped_column(Text(), nullable=True)
    token_hash: Mapped[str] = mapped_column(Text(), nullable=False)
    authentication_methods: Mapped[list[str]] = mapped_column(
        JSONB(),
        nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return a representation without grant or subject digests."""
        return f"AuthGrant(grant_id={self.grant_id!r}, purpose={self.purpose!r})"
