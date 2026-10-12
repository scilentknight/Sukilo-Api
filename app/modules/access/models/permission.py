"""Define immutable authorization permission persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.access.types import PermissionStatus


class Permission(Base):
    """Store one stable capability definition."""

    __tablename__ = "permission"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'RETIRED')",
            name="ck_permission_status",
        ),
        CheckConstraint(
            "code ~ '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$'",
            name="ck_permission_code_format",
        ),
        Index("uq_permission_code", "code", unique=True),
        Index("ix_permission_status", "status"),
        {"schema": "iam"},
    )

    permission_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[PermissionStatus] = mapped_column(
        String(20),
        nullable=False,
        default=PermissionStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
