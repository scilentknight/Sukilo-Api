"""Define narrow assignment service contracts."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.access.models import Permission, Role, UserRoleAssignment
from app.modules.access.policies import AssignmentProjection
from app.modules.access.types import ScopeType


class AssignmentRepositoryPort(Protocol):
    """Describe assignment persistence used by the service."""

    def get_role_by_code_for_update(self, code: str) -> Role | None:
        """Lock and return one role."""
        ...

    def list_active_roles(self) -> list[Role]:
        """Return stable active roles."""
        ...

    def list_active_permissions(self) -> list[Permission]:
        """Return stable active permissions."""
        ...

    def list_assignments(
        self,
        *,
        user_id: UUID | None,
        country_id: UUID | None,
        cursor: UUID | None,
        limit: int,
    ) -> list[UserRoleAssignment]:
        """Return a bounded assignment page matching explicit filters."""
        ...

    def get_role_permissions(self, role_id: UUID) -> frozenset[str]:
        """Return active role capabilities."""
        ...

    def list_active_projections(
        self,
        user_id: UUID,
        now: datetime,
    ) -> list[AssignmentProjection]:
        """Return current authorization projections."""
        ...

    def list_active_role_codes(self, user_id: UUID, now: datetime) -> list[str]:
        """Return the caller's current role codes."""
        ...

    def acquire_overlap_lock(
        self,
        user_id: UUID,
        role_id: UUID,
        scope_type: ScopeType,
        scope_id: UUID | None,
    ) -> None:
        """Serialize one assignment identity."""
        ...

    def acquire_bootstrap_lock(self, role_id: UUID) -> None:
        """Serialize bootstrap decisions for one role."""
        ...

    def has_active_role_assignment(
        self,
        role_id: UUID,
        now: datetime,
    ) -> bool:
        """Return whether one role has any current assignment."""
        ...

    def has_active_overlap(self, **criteria: object) -> bool:
        """Return whether a requested validity range overlaps."""
        ...

    def add(self, assignment: UserRoleAssignment) -> None:
        """Stage one assignment."""
        ...

    def get_assignment_for_update(
        self,
        assignment_id: UUID,
    ) -> UserRoleAssignment | None:
        """Lock and return one assignment."""
        ...

    def flush(self) -> None:
        """Flush staged assignment state."""
        ...


class AccountRepositoryPort(Protocol):
    """Describe account locking used by assignment mutations."""

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> object:
        """Return one account."""
        ...


class AuthorizationCachePort(Protocol):
    """Describe versioned authorization invalidation."""

    def invalidate(self, user_id: UUID, auth_version: int) -> None:
        """Invalidate one account authorization version."""
        ...


Clock = Callable[[], datetime]
