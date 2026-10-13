"""Define customer data export request persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.customers.types import DataExportStatus

_EXPORT_STATUSES = ", ".join(f"'{member.value}'" for member in DataExportStatus)


class DataExportRequest(Base):
    """Store one asynchronous, country-scoped customer data export request."""

    __tablename__ = "data_export_request"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_EXPORT_STATUSES})",
            name="ck_data_export_request_status",
        ),
        UniqueConstraint(
            "user_id",
            "country_id",
            "idempotency_key",
            name="uq_data_export_request_user_country_idempotency",
        ),
        Index("ix_data_export_request_user_status", "user_id", "status"),
        Index(
            "ix_data_export_request_artifact_expires_at",
            "artifact_expires_at",
        ),
        {"schema": "iam"},
    )

    export_request_id: Mapped[UUID] = mapped_column(
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
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[DataExportStatus] = mapped_column(
        String(24),
        nullable=False,
    )
    artifact_reference: Mapped[str | None] = mapped_column(Text(), nullable=True)
    artifact_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    def __repr__(self) -> str:
        """Return a representation that excludes artifact references."""
        return (
            f"DataExportRequest(export_request_id={self.export_request_id!r}, "
            f"status={self.status!r})"
        )
