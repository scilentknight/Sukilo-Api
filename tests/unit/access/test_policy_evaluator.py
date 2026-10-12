"""Test permission and resource-scope authorization decisions."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.auth_context import AuthContext
from app.modules.access.policies import (
    AssignmentProjection,
    AuthorizationRequest,
    PolicyEvaluator,
    ResourceScope,
)
from app.modules.access.types import ScopeType


NOW = datetime(2026, 10, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
COUNTRY_ID = UUID(int=3)
OTHER_COUNTRY_ID = UUID(int=4)


def _principal() -> AuthContext:
    return AuthContext(
        user_id=USER_ID,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("password",),
        authenticated_at=NOW,
        correlation_id=UUID(int=5),
    )


def _scope(country_id: UUID) -> ResourceScope:
    return ResourceScope(
        scope_type=ScopeType.COUNTRY,
        scope_id=country_id,
        country_id=country_id,
        vendor_id=None,
    )


def _projection(
    *,
    scope: ResourceScope,
    permissions: frozenset[str],
    valid_to: datetime | None = None,
) -> AssignmentProjection:
    return AssignmentProjection(
        assignment_id=UUID(int=6),
        scope=scope,
        permissions=permissions,
        valid_from=None,
        valid_to=valid_to,
        revoked_at=None,
    )


def test_missing_permission_denies_even_with_matching_scope() -> None:
    """Require permission mappings rather than role presence."""
    request = AuthorizationRequest(
        principal=_principal(),
        permission_code="iam.assignment.read",
        resource_scope=_scope(COUNTRY_ID),
        resource_owner_id=None,
    )
    decision = PolicyEvaluator(clock=lambda: NOW).evaluate(
        request,
        [_projection(scope=_scope(COUNTRY_ID), permissions=frozenset())],
    )
    assert decision.allowed is False
    assert decision.reason_code == "PERMISSION_MISSING"


def test_country_assignment_does_not_cross_country() -> None:
    """Deny a capability when its assignment belongs to another country."""
    request = AuthorizationRequest(
        principal=_principal(),
        permission_code="iam.assignment.read",
        resource_scope=_scope(OTHER_COUNTRY_ID),
        resource_owner_id=None,
    )
    decision = PolicyEvaluator(clock=lambda: NOW).evaluate(
        request,
        [
            _projection(
                scope=_scope(COUNTRY_ID),
                permissions=frozenset({"iam.assignment.read"}),
            )
        ],
    )
    assert decision.allowed is False
    assert decision.reason_code == "SCOPE_MISMATCH"


def test_global_permission_covers_country_read() -> None:
    """Allow a mapped global read against an explicit country resource."""
    global_scope = ResourceScope(ScopeType.GLOBAL, None, None, None)
    request = AuthorizationRequest(
        principal=_principal(),
        permission_code="iam.assignment.read",
        resource_scope=_scope(COUNTRY_ID),
        resource_owner_id=None,
    )
    decision = PolicyEvaluator(clock=lambda: NOW).evaluate(
        request,
        [
            _projection(
                scope=global_scope,
                permissions=frozenset({"iam.assignment.read"}),
            )
        ],
    )
    assert decision.allowed is True


def test_global_destructive_capability_authorizes_global_resource() -> None:
    """Allow a global capability against a genuinely global resource."""
    global_scope = ResourceScope(ScopeType.GLOBAL, None, None, None)
    request = AuthorizationRequest(
        principal=_principal(),
        permission_code="iam.assignment.grant",
        resource_scope=global_scope,
        resource_owner_id=None,
    )
    decision = PolicyEvaluator(clock=lambda: NOW).evaluate(
        request,
        [
            _projection(
                scope=global_scope,
                permissions=frozenset({"iam.assignment.grant"}),
            )
        ],
    )
    assert decision.allowed is True


def test_global_assignment_authorizes_explicit_country_resource() -> None:
    """Allow global authority against an explicitly scoped country resource."""
    global_scope = ResourceScope(ScopeType.GLOBAL, None, None, None)
    request = AuthorizationRequest(
        principal=_principal(),
        permission_code="iam.assignment.grant",
        resource_scope=_scope(COUNTRY_ID),
        resource_owner_id=None,
    )
    decision = PolicyEvaluator(clock=lambda: NOW).evaluate(
        request,
        [
            _projection(
                scope=global_scope,
                permissions=frozenset({"iam.assignment.grant"}),
            )
        ],
    )
    assert decision.allowed is True


def test_expired_assignment_is_not_active() -> None:
    """Deny permissions whose assignment validity ended."""
    request = AuthorizationRequest(
        principal=_principal(),
        permission_code="iam.assignment.read",
        resource_scope=_scope(COUNTRY_ID),
        resource_owner_id=None,
    )
    decision = PolicyEvaluator(clock=lambda: NOW).evaluate(
        request,
        [
            _projection(
                scope=_scope(COUNTRY_ID),
                permissions=frozenset({"iam.assignment.read"}),
                valid_to=NOW - timedelta(seconds=1),
            )
        ],
    )
    assert decision.allowed is False
    assert decision.reason_code == "ASSIGNMENT_INACTIVE"
