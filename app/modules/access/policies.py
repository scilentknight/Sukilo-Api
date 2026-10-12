"""Evaluate permission mappings against trusted resource scopes."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.auth_context import AuthContext
from app.modules.access.types import ScopeType


class ScopeValidationError(ValueError):
    """Report an invalid or incomplete trusted scope."""


class ResourceNotFoundPolicyError(Exception):
    """Hide a resource that is owned by another account."""


@dataclass(frozen=True, slots=True)
class ResourceScope:
    """Describe a trusted resource and its ownership ancestry."""

    scope_type: ScopeType
    scope_id: UUID | None
    country_id: UUID | None
    vendor_id: UUID | None

    def __post_init__(self) -> None:
        """Reject missing or conflicting scope ancestry."""
        valid = {
            ScopeType.GLOBAL: (
                self.scope_id is None
                and self.country_id is None
                and self.vendor_id is None
            ),
            ScopeType.COUNTRY: (
                self.scope_id is not None
                and self.scope_id == self.country_id
                and self.vendor_id is None
            ),
            ScopeType.VENDOR: (
                self.scope_id is not None
                and self.scope_id == self.vendor_id
                and self.country_id is not None
            ),
            ScopeType.STORE: (
                self.scope_id is not None
                and self.country_id is not None
                and self.vendor_id is not None
            ),
        }
        if not isinstance(self.scope_type, ScopeType) or not valid.get(
            self.scope_type,
            False,
        ):
            raise ScopeValidationError("Resource scope is invalid.")


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Describe one permission decision against trusted context."""

    principal: AuthContext
    permission_code: str
    resource_scope: ResourceScope
    resource_owner_id: UUID | None


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Return an internal allow or deny result."""

    allowed: bool
    reason_code: str
    assignment_id: UUID | None


@dataclass(frozen=True, slots=True)
class AssignmentProjection:
    """Carry the minimum assignment data needed for authorization."""

    assignment_id: UUID
    scope: ResourceScope
    permissions: frozenset[str]
    valid_from: datetime | None
    valid_to: datetime | None
    revoked_at: datetime | None


class PolicyEvaluator:
    """Apply deny-by-default RBAC and ABAC scope rules."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        """Initialize the evaluator.

        Args:
            clock: UTC time source.
        """
        self.clock = clock

    def evaluate(
        self,
        request: AuthorizationRequest,
        projections: Iterable[AssignmentProjection],
    ) -> AuthorizationDecision:
        """Evaluate active assignments for one permission and resource.

        Args:
            request: Trusted principal and resource request.
            projections: Resolved assignment projections.

        Returns:
            Internal authorization decision.
        """
        now = self.clock()
        permission_seen = False
        inactive_seen = False
        for projection in projections:
            if request.permission_code not in projection.permissions:
                continue
            permission_seen = True
            if not _is_active(projection, now):
                inactive_seen = True
                continue
            if _scope_covers(projection.scope, request.resource_scope):
                return AuthorizationDecision(
                    allowed=True,
                    reason_code="ALLOWED",
                    assignment_id=projection.assignment_id,
                )
        if inactive_seen:
            return _deny("ASSIGNMENT_INACTIVE")
        if permission_seen:
            return _deny("SCOPE_MISMATCH")
        return _deny("PERMISSION_MISSING")


def require_self(principal_user_id: UUID, resource_user_id: UUID) -> None:
    """Require a resource to belong to the authenticated account.

    Args:
        principal_user_id: Authenticated account ID.
        resource_user_id: Resource owner account ID.

    Raises:
        ResourceNotFoundPolicyError: If the owner differs.
    """
    _require_owner(principal_user_id, resource_user_id)


def require_session_owner(
    principal_user_id: UUID,
    session_user_id: UUID,
) -> None:
    """Require a session to belong to the authenticated account.

    Args:
        principal_user_id: Authenticated account ID.
        session_user_id: Session owner account ID.

    Raises:
        ResourceNotFoundPolicyError: If the owner differs.
    """
    _require_owner(principal_user_id, session_user_id)


def require_address_owner(
    principal_user_id: UUID,
    address_user_id: UUID,
) -> None:
    """Require an address to belong to the authenticated account.

    Args:
        principal_user_id: Authenticated account ID.
        address_user_id: Address owner account ID.

    Raises:
        ResourceNotFoundPolicyError: If the owner differs.
    """
    _require_owner(principal_user_id, address_user_id)


def _require_owner(principal_user_id: UUID, resource_user_id: UUID) -> None:
    if principal_user_id != resource_user_id:
        raise ResourceNotFoundPolicyError("Resource is unavailable.")


def _is_active(projection: AssignmentProjection, now: datetime) -> bool:
    return (
        projection.revoked_at is None
        and (projection.valid_from is None or projection.valid_from <= now)
        and (projection.valid_to is None or projection.valid_to > now)
    )


def _scope_covers(assigned: ResourceScope, resource: ResourceScope) -> bool:
    if assigned.scope_type == ScopeType.GLOBAL:
        return True
    if assigned.scope_type == ScopeType.COUNTRY:
        return assigned.country_id == resource.country_id
    if assigned.scope_type == ScopeType.VENDOR:
        return (
            assigned.country_id == resource.country_id
            and assigned.vendor_id == resource.vendor_id
        )
    return (
        assigned.scope_id == resource.scope_id
        and assigned.country_id == resource.country_id
        and assigned.vendor_id == resource.vendor_id
    )


def _deny(reason_code: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        allowed=False,
        reason_code=reason_code,
        assignment_id=None,
    )
