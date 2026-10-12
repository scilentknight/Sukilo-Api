"""Define scoped authorization role persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.access.types import RoleStatus, ScopeType


class Role(Base):
    """Store one stable role definition."""

    __tablename__ = "role"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('GLOBAL', 'COUNTRY', 'VENDOR', 'STORE')",
            name="ck_role_scope_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'RETIRED')",
            name="ck_role_status",
        ),
        Index("uq_role_code", "code", unique=True),
        Index("ix_role_status", "status"),
        {"schema": "iam"},
    )

    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scope_type: Mapped[ScopeType] = mapped_column(
        String(24),
        nullable=False,
    )
    status: Mapped[RoleStatus] = mapped_column(
        String(20),
        nullable=False,
        default=RoleStatus.ACTIVE,
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=text("true"),
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
