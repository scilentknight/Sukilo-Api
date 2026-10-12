"""Define scoped user-role assignment persistence."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.access.types import ScopeType


class UserRoleAssignment(Base):
    """Store one durable scoped role grant."""

    __tablename__ = "user_role_assignment"
    __table_args__ = (
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_to > valid_from",
            name="ck_assignment_validity_window",
        ),
        CheckConstraint(
            "("
            "scope_type = 'GLOBAL' AND scope_id IS NULL "
            "AND country_id IS NULL AND vendor_id IS NULL"
            ") OR ("
            "scope_type = 'COUNTRY' AND scope_id = country_id "
            "AND country_id IS NOT NULL AND vendor_id IS NULL"
            ") OR ("
            "scope_type = 'VENDOR' AND scope_id = vendor_id "
            "AND country_id IS NOT NULL AND vendor_id IS NOT NULL"
            ") OR ("
            "scope_type = 'STORE' AND scope_id IS NOT NULL "
            "AND country_id IS NOT NULL AND vendor_id IS NOT NULL"
            ")",
            name="ck_assignment_scope_shape",
        ),
        Index(
            "ix_assignment_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_assignment_country_user_active",
            "country_id",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_assignment_vendor_user_active",
            "vendor_id",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_assignment_role_scope_active",
            "role_id",
            "scope_type",
            "scope_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        {"schema": "iam"},
    )

    assignment_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.role.role_id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_type: Mapped[ScopeType] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    country_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    vendor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("iam.user_account.user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    revocation_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=1,
        server_default=text("1"),
    )
