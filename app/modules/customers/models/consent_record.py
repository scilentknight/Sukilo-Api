"""Define append-only consent evidence persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.customers.types import ConsentSource, ConsentType


_CONSENT_TYPES = ", ".join(f"'{member.value}'" for member in ConsentType)
_CONSENT_SOURCES = ", ".join(f"'{member.value}'" for member in ConsentSource)


class ConsentRecord(Base):
    """Store one immutable consent acknowledgement or withdrawal."""

    __tablename__ = "consent_record"
    __table_args__ = (
        CheckConstraint(
            f"consent_type IN ({_CONSENT_TYPES})",
            name="ck_consent_type",
        ),
        CheckConstraint(
            f"source IN ({_CONSENT_SOURCES})",
            name="ck_consent_source",
        ),
        Index(
            "ix_consent_user_country_type_latest",
            "user_id",
            "country_id",
            "consent_type",
            "recorded_at",
            "consent_id",
            postgresql_ops={
                "recorded_at": "DESC",
                "consent_id": "DESC",
            },
        ),
        Index(
            "ix_consent_country_type_recorded",
            "country_id",
            "consent_type",
            "recorded_at",
        ),
        {"schema": "iam"},
    )

    consent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    country_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    consent_type: Mapped[ConsentType] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    source: Mapped[ConsentSource] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
