"""Define public access-control API payloads."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.access.types import ScopeType


class RoleResponse(BaseModel):
    """Expose one active role definition."""

    role_id: UUID
    code: str
    name: str
    scope_type: ScopeType
    model_config = ConfigDict(from_attributes=True)


class PermissionResponse(BaseModel):
    """Expose one stable permission definition."""

    permission_id: UUID
    code: str
    description: str
    model_config = ConfigDict(from_attributes=True)


class AssignmentResponse(BaseModel):
    """Expose safe scoped assignment state."""

    assignment_id: UUID
    user_id: UUID
    role_id: UUID
    role_code: str
    scope_type: ScopeType
    scope_id: UUID | None
    country_id: UUID | None
    vendor_id: UUID | None
    valid_from: datetime | None
    valid_to: datetime | None
    revoked_at: datetime | None
    version: int


class GrantAssignmentRequest(BaseModel):
    """Accept one public scoped assignment grant."""

    user_id: UUID
    role_code: str = Field(min_length=1, max_length=64)
    scope_type: ScopeType
    scope_id: UUID | None = None
    country_id: UUID | None = None
    vendor_id: UUID | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class RevokeAssignmentRequest(BaseModel):
    """Accept a required assignment revocation reason."""

    reason: str = Field(min_length=1, max_length=255)
