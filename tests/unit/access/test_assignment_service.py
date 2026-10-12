"""Test assignment administration invariants."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.access.policies import AssignmentProjection, ResourceScope
from app.modules.access.service import (
    AssignmentConflictError,
    AssignmentDeniedError,
    AssignmentQuery,
    AssignmentService,
    AssignmentValidationError,
    GrantAssignment,
    RevokeAssignment,
)
from app.modules.access.types import RoleStatus, ScopeType
from app.modules.identity.types import AccountStatus, GrantPurpose


NOW = datetime(2026, 10, 12, tzinfo=UTC)
ACTOR_ID = UUID(int=1)
TARGET_ID = UUID(int=2)
SESSION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)
OTHER_COUNTRY_ID = UUID(int=5)
ROLE_ID = UUID(int=6)
ASSIGNMENT_ID = UUID(int=7)


class Account:
    """Provide mutable account state to the service."""

    def __init__(self, user_id: UUID, auth_version: int = 1) -> None:
        """Initialize account state."""
        self.user_id = user_id
        self.auth_version = auth_version
        self.status = AccountStatus.ACTIVE


class Role:
    """Provide role state to the service."""

    code = "COUNTRY_OPERATIONS"
    role_id = ROLE_ID
    scope_type = ScopeType.COUNTRY
    status = RoleStatus.ACTIVE


class AccountsFake:
    """Return lockable accounts."""

    def __init__(self) -> None:
        """Initialize known accounts."""
        self.rows = {TARGET_ID: Account(TARGET_ID), ACTOR_ID: Account(ACTOR_ID)}

    def get_by_id(self, user_id: UUID, *, for_update: bool = False):
        """Return an account."""
        del for_update
        return self.rows.get(user_id)

    def flush(self) -> None:
        """Accept account changes."""


class Assignment:
    """Provide mutable assignment state."""

    assignment_id = ASSIGNMENT_ID
    user_id = TARGET_ID
    role_id = ROLE_ID
    scope_type = ScopeType.COUNTRY
    scope_id = COUNTRY_ID
    country_id = COUNTRY_ID
    vendor_id = None
    valid_from = None
    valid_to = None
    revoked_at = None
    revoked_by = None
    revocation_reason = None
    version = 1


class RepositoryFake:
    """Record assignment persistence operations."""

    def __init__(self, permissions: frozenset[str] | None = None) -> None:
        """Initialize repository state."""
        self.role = Role()
        self.permissions = permissions or frozenset({"catalog.product.write"})
        self.assignment = Assignment()
        self.added = []
        self.overlap = False
        self.locked = False
        self.list_calls = []
        self.actor_scope = _scope(COUNTRY_ID)
        self.actor_permissions = frozenset(
            {
                "iam.assignment.grant",
                "iam.assignment.revoke",
                "catalog.product.write",
            }
        )

    def list_active_roles(self) -> list[Role]:
        """Return the active role catalog."""
        return [self.role]

    def list_active_permissions(self) -> list[object]:
        """Return an empty permission catalog."""
        return []

    def list_assignments(
        self,
        *,
        user_id: UUID | None,
        country_id: UUID | None,
        cursor: UUID | None,
        limit: int,
    ) -> list[Assignment]:
        """Record bounded assignment list filters."""
        self.list_calls.append((user_id, country_id, cursor, limit))
        return [self.assignment]

    def get_role_by_code_for_update(self, code: str):
        """Return the configured role."""
        return self.role if code == self.role.code else None

    def get_role_permissions(self, role_id: UUID) -> frozenset[str]:
        """Return target role capabilities."""
        assert role_id == ROLE_ID
        return self.permissions

    def list_active_projections(
        self,
        user_id: UUID,
        now: datetime,
    ) -> list[AssignmentProjection]:
        """Return actor authority in one country."""
        assert user_id == ACTOR_ID
        return [
            AssignmentProjection(
                assignment_id=UUID(int=10),
                scope=self.actor_scope,
                permissions=self.actor_permissions,
                valid_from=None,
                valid_to=now + timedelta(hours=1),
                revoked_at=None,
            )
        ]

    def acquire_overlap_lock(
        self,
        user_id: UUID,
        role_id: UUID,
        scope_type: ScopeType,
        scope_id: UUID | None,
    ) -> None:
        """Record transaction advisory locking."""
        del user_id, role_id, scope_type, scope_id
        self.locked = True

    def has_active_overlap(self, **kwargs) -> bool:
        """Return configured overlap state."""
        del kwargs
        return self.overlap

    def add(self, assignment) -> None:
        """Record a staged assignment."""
        self.added.append(assignment)

    def get_assignment_for_update(self, assignment_id: UUID):
        """Return a lockable assignment."""
        return self.assignment if assignment_id == ASSIGNMENT_ID else None

    def flush(self) -> None:
        """Accept staged changes."""


class GrantsFake:
    """Record consumed step-up bindings."""

    def __init__(self, error: Exception | None = None) -> None:
        """Initialize consumption records."""
        self.calls = []
        self.error = error

    def consume(self, raw_token: str, **kwargs):
        """Record one grant consumption."""
        self.calls.append((raw_token, kwargs))
        if self.error is not None:
            raise self.error
        return object()


class CountriesFake:
    """Recognize one active country."""

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Return whether assignment management is enabled."""
        assert operation == "iam.assignment.manage"
        return country_id == COUNTRY_ID


class AuditFake:
    """Record atomic audit events."""

    def __init__(self) -> None:
        """Initialize empty audit records."""
        self.records = []

    def stage(self, event, transaction: DatabaseTransaction) -> None:
        """Record one event and its transaction."""
        self.records.append((event, transaction))


class CacheFake:
    """Record authorization cache invalidation."""

    def __init__(self) -> None:
        """Initialize invalidation records."""
        self.invalidated = []

    def invalidate(self, user_id: UUID, auth_version: int) -> None:
        """Record one versioned key invalidation."""
        self.invalidated.append((user_id, auth_version))


def _scope(country_id: UUID) -> ResourceScope:
    return ResourceScope(ScopeType.COUNTRY, country_id, country_id, None)


def _context() -> AuthContext:
    return AuthContext(
        user_id=ACTOR_ID,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("password", "otp"),
        authenticated_at=NOW,
        correlation_id=UUID(int=8),
    )


def _service(repository: RepositoryFake | None = None):
    repository = repository or RepositoryFake()
    accounts = AccountsFake()
    grants = GrantsFake()
    audit = AuditFake()
    cache = CacheFake()
    transaction = DatabaseTransaction(object())
    service = AssignmentService(
        repository=repository,
        accounts=accounts,
        grants=grants,
        countries=CountriesFake(),
        policy_evaluator=__import__(
            "app.modules.access.policies",
            fromlist=["PolicyEvaluator"],
        ).PolicyEvaluator(lambda: NOW),
        audit_writer=audit,
        transaction=transaction,
        cache=cache,
        clock=lambda: NOW,
    )
    return service, repository, accounts, grants, audit, cache, transaction


def _grant(**overrides) -> GrantAssignment:
    values = {
        "actor": _context(),
        "target_user_id": TARGET_ID,
        "role_code": "COUNTRY_OPERATIONS",
        "scope": _scope(COUNTRY_ID),
        "valid_from": None,
        "valid_to": None,
        "step_up_token": "step-up-token",
    }
    values.update(overrides)
    return GrantAssignment(**values)


def test_grant_consumes_bound_step_up_and_stages_atomic_audit() -> None:
    """Grant, version, audit, and invalidate one assignment."""
    service, repository, accounts, grants, audit, cache, transaction = _service()
    view = service.grant_assignment(_grant())
    assert view.user_id == TARGET_ID
    assert repository.locked is True
    assert accounts.rows[TARGET_ID].auth_version == 2
    assert grants.calls == [
        (
            "step-up-token",
            {
                "user_id": ACTOR_ID,
                "session_id": SESSION_ID,
                "purpose": GrantPurpose.STEP_UP,
                "action_class": "iam.assignment.grant",
                "subject_hash": None,
            },
        )
    ]
    assert [record[0].action for record in audit.records] == [
        "iam.authorization.step_up_completed",
        "iam.assignment.granted",
    ]
    assert all(record[1] is transaction for record in audit.records)
    assert all(
        "step_up_token" not in record[0].after_data
        for record in audit.records
    )
    assert cache.invalidated == []
    service.commit_invalidations()
    assert cache.invalidated == [(TARGET_ID, 1)]


def test_catalog_read_uses_explicit_country_scope() -> None:
    """Authorize catalog reads in an explicitly requested country."""
    repository = RepositoryFake()
    repository.actor_permissions = frozenset(
        {"iam.role.read", "iam.permission.read"}
    )
    service, *_ = _service(repository)
    assert service.list_roles(_context(), COUNTRY_ID) == [repository.role]
    assert service.list_permissions(_context(), COUNTRY_ID) == []


def test_catalog_read_without_country_requires_global_assignment() -> None:
    """Deny an unscoped catalog read to country-only authority."""
    repository = RepositoryFake()
    repository.actor_permissions = frozenset({"iam.role.read"})
    service, *_ = _service(repository)
    with pytest.raises(AssignmentDeniedError):
        service.list_roles(_context())
    repository.actor_scope = ResourceScope(
        ScopeType.GLOBAL,
        None,
        None,
        None,
    )
    assert service.list_roles(_context()) == [repository.role]


def test_user_only_assignment_list_requires_global_read() -> None:
    """Deny cross-country user lookup unless global read is proven first."""
    repository = RepositoryFake()
    repository.actor_permissions = frozenset({"iam.assignment.read"})
    service, *_ = _service(repository)
    query = AssignmentQuery(_context(), TARGET_ID, None, 25, UUID(int=30))
    with pytest.raises(AssignmentDeniedError):
        service.list_assignments(query)
    assert repository.list_calls == []
    repository.actor_scope = ResourceScope(ScopeType.GLOBAL, None, None, None)
    views = service.list_assignments(query)
    assert len(views) == 1
    assert repository.list_calls == [(TARGET_ID, None, UUID(int=30), 25)]


def test_country_assignment_list_filters_sql_query_by_country() -> None:
    """Pass a proven country boundary into persistence before rows load."""
    repository = RepositoryFake()
    repository.actor_permissions = frozenset({"iam.assignment.read"})
    service, *_ = _service(repository)
    query = AssignmentQuery(_context(), TARGET_ID, COUNTRY_ID, 50, None)
    views = service.list_assignments(query)
    assert len(views) == 1
    assert repository.list_calls == [(TARGET_ID, COUNTRY_ID, None, 50)]


@pytest.mark.parametrize(
    "action",
    [
        "iam.authorization.step_up_failed",
        "iam.assignment.grant_denied",
    ],
)
def test_failure_audit_is_redacted_and_transaction_bound(
    action: str,
) -> None:
    """Stage safe denial evidence without retaining the step-up token."""
    service, _, _, _, audit, _, transaction = _service()
    command = _grant(step_up_token="never-store-this")
    service.stage_failure_audit(command, action)
    event, staged_transaction = audit.records[0]
    assert event.action == action
    assert event.resource_id == TARGET_ID
    assert "never-store-this" not in repr(event)
    assert staged_transaction is transaction


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"target_user_id": ACTOR_ID}, "self"),
        ({"role_code": "SUPER_ADMIN"}, "SUPER_ADMIN"),
        ({"scope": _scope(OTHER_COUNTRY_ID)}, "country"),
    ],
)
def test_grant_rejects_public_security_boundaries(overrides, message) -> None:
    """Block self, super-admin, and cross-country grants."""
    service, *_ = _service()
    with pytest.raises(AssignmentDeniedError, match=message):
        service.grant_assignment(_grant(**overrides))


def test_grant_rejects_capability_delegation_actor_lacks() -> None:
    """Prevent delegation of an unauthorized role capability."""
    repository = RepositoryFake(
        frozenset({"finance.payout.release"}),
    )
    service, *_ = _service(repository)
    with pytest.raises(AssignmentDeniedError, match="capability"):
        service.grant_assignment(_grant())


def test_grant_rejects_overlapping_active_assignment() -> None:
    """Reject a duplicate validity window under the advisory lock."""
    repository = RepositoryFake()
    repository.overlap = True
    service, *_ = _service(repository)
    with pytest.raises(AssignmentConflictError, match="overlap"):
        service.grant_assignment(_grant())


def test_revoke_requires_reason_and_updates_version() -> None:
    """Require a reason and atomically revoke a public assignment."""
    service, repository, accounts, grants, audit, cache, _ = _service()
    with pytest.raises(AssignmentValidationError, match="reason"):
        service.revoke_assignment(
            RevokeAssignment(
                actor=_context(),
                assignment_id=ASSIGNMENT_ID,
                reason=" ",
                step_up_token="step-up-token",
            )
        )
    view = service.revoke_assignment(
        RevokeAssignment(
            actor=_context(),
            assignment_id=ASSIGNMENT_ID,
            reason="Access no longer needed",
            step_up_token="step-up-token",
        )
    )
    assert view.revoked_at == NOW
    assert repository.assignment.revocation_reason == "Access no longer needed"
    assert accounts.rows[TARGET_ID].auth_version == 2
    assert grants.calls[-1][1]["action_class"] == "iam.assignment.revoke"
    assert audit.records[-1][0].action == "iam.assignment.revoked"
    assert cache.invalidated == []
    service.commit_invalidations()
    assert cache.invalidated == [(TARGET_ID, 1)]
