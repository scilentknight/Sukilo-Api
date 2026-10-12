"""Test the offline super-administrator bootstrap service."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.commands.bootstrap_super_admin import (
    SuperAdminBootstrapCommand,
    SuperAdminBootstrapError,
    SuperAdminBootstrapService,
    bootstrap_super_admin,
)
from app.core.transactions import DatabaseTransaction
from app.modules.access.catalog import ROLES
from app.modules.access.types import RoleStatus, ScopeType
from app.modules.identity.types import AccountStatus
from app.ports.audit import NoOpAuditWriter


NOW = datetime(2026, 10, 12, tzinfo=UTC)
TARGET_ID = UUID(int=20)
CORRELATION_ID = UUID(int=21)
SUPER_ADMIN_ROLE = next(role for role in ROLES if role.code == "SUPER_ADMIN")


class Account:
    """Provide mutable verified account state."""

    user_id = TARGET_ID
    status = AccountStatus.ACTIVE
    email_verified_at = NOW
    phone_verified_at = None
    auth_version = 3


class Role:
    """Provide deterministic super-administrator role state."""

    role_id = SUPER_ADMIN_ROLE.role_id
    code = "SUPER_ADMIN"
    scope_type = ScopeType.GLOBAL
    status = RoleStatus.ACTIVE


class AccountsFake:
    """Return one lockable target account."""

    def __init__(self) -> None:
        """Initialize the target account."""
        self.account = Account()

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> Account | None:
        """Return the target when requested for update."""
        assert for_update is True
        return self.account if user_id == TARGET_ID else None


class RepositoryFake:
    """Record bootstrap locking and assignment persistence."""

    def __init__(self) -> None:
        """Initialize repository state."""
        self.role = Role()
        self.active_exists = False
        self.bootstrap_locked = False
        self.overlap_locked = False
        self.added = []

    def get_role_by_code_for_update(self, code: str) -> Role | None:
        """Return the deterministic role."""
        return self.role if code == "SUPER_ADMIN" else None

    def acquire_bootstrap_lock(self, role_id: UUID) -> None:
        """Record the global bootstrap lock."""
        assert role_id == SUPER_ADMIN_ROLE.role_id
        self.bootstrap_locked = True

    def has_active_role_assignment(self, role_id: UUID, now: datetime) -> bool:
        """Return configured active-super-admin state."""
        assert role_id == SUPER_ADMIN_ROLE.role_id
        assert now == NOW
        return self.active_exists

    def acquire_overlap_lock(self, *args: object) -> None:
        """Record the target assignment overlap lock."""
        self.overlap_locked = True

    def has_active_overlap(self, **criteria: object) -> bool:
        """Return no duplicate target assignment."""
        del criteria
        return False

    def add(self, assignment: object) -> None:
        """Record the staged assignment."""
        self.added.append(assignment)

    def flush(self) -> None:
        """Accept staged state."""


class AuditFake:
    """Record transaction-bound audit evidence."""

    def __init__(self) -> None:
        """Initialize audit records."""
        self.records = []

    def stage(self, event: object, transaction: DatabaseTransaction) -> None:
        """Record one staged event."""
        self.records.append((event, transaction))


def _service() -> tuple[
    SuperAdminBootstrapService,
    RepositoryFake,
    AccountsFake,
    AuditFake,
    DatabaseTransaction,
]:
    repository = RepositoryFake()
    accounts = AccountsFake()
    audit = AuditFake()
    transaction = DatabaseTransaction(object())
    service = SuperAdminBootstrapService(
        repository=repository,
        accounts=accounts,
        audit_writer=audit,
        transaction=transaction,
        clock=lambda: NOW,
    )
    return service, repository, accounts, audit, transaction


def _command(**overrides: object) -> SuperAdminBootstrapCommand:
    values = {
        "target_user_id": TARGET_ID,
        "offline_approval_reference": "CAB-2026-10-12",
        "allow_recovery": False,
        "correlation_id": CORRELATION_ID,
    }
    values.update(overrides)
    return SuperAdminBootstrapCommand(**values)


def test_bootstrap_stages_global_assignment_version_and_redacted_audit() -> None:
    """Create the deterministic role assignment and atomic audit evidence."""
    service, repository, accounts, audit, transaction = _service()
    assignment_id = service.bootstrap(_command())
    assignment = repository.added[0]
    assert assignment.assignment_id == assignment_id
    assert assignment.role_id == SUPER_ADMIN_ROLE.role_id
    assert assignment.scope_type == ScopeType.GLOBAL
    assert repository.bootstrap_locked is True
    assert repository.overlap_locked is True
    assert accounts.account.auth_version == 4
    event = audit.records[0][0]
    assert event.action == "iam.super_admin.bootstrapped"
    assert event.after_data == {
        "role_code": "SUPER_ADMIN",
        "scope_type": "GLOBAL",
        "offline_approval_recorded": True,
        "recovery": False,
    }
    assert audit.records[0][1] is transaction


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda account: setattr(account, "status", AccountStatus.SUSPENDED), "active"),
        (lambda account: setattr(account, "email_verified_at", None), "verified"),
    ],
)
def test_bootstrap_requires_active_verified_account(mutate, message: str) -> None:
    """Reject an inactive or unverified target account."""
    service, _, accounts, _, _ = _service()
    mutate(accounts.account)
    accounts.account.phone_verified_at = None
    with pytest.raises(SuperAdminBootstrapError, match=message):
        service.bootstrap(_command())


def test_bootstrap_requires_offline_approval_and_recovery_override() -> None:
    """Require approval and explicitly gate disaster recovery."""
    service, repository, _, _, _ = _service()
    with pytest.raises(SuperAdminBootstrapError, match="approval"):
        service.bootstrap(_command(offline_approval_reference=" "))
    repository.active_exists = True
    with pytest.raises(SuperAdminBootstrapError, match="already exists"):
        service.bootstrap(_command())
    assignment_id = service.bootstrap(_command(allow_recovery=True))
    assert isinstance(assignment_id, UUID)


def test_bootstrap_service_rejects_noop_audit_before_mutation() -> None:
    """Reject a non-durable audit adapter before touching repository state."""
    repository = RepositoryFake()
    accounts = AccountsFake()
    with pytest.raises(SuperAdminBootstrapError, match="audit"):
        SuperAdminBootstrapService(
            repository=repository,
            accounts=accounts,
            audit_writer=NoOpAuditWriter(),
            transaction=DatabaseTransaction(object()),
            clock=lambda: NOW,
        )
    assert repository.bootstrap_locked is False
    assert repository.added == []
    assert accounts.account.auth_version == 3


def test_bootstrap_command_rejects_noop_audit_before_commit() -> None:
    """Reject non-durable bootstrap auditing before database interaction."""
    class DatabaseFake:
        """Record transaction calls."""

        def __init__(self) -> None:
            """Initialize call counts."""
            self.commits = 0
            self.rollbacks = 0

        def commit(self) -> None:
            """Record a commit."""
            self.commits += 1

        def rollback(self) -> None:
            """Record a rollback."""
            self.rollbacks += 1

    db = DatabaseFake()
    with pytest.raises(SuperAdminBootstrapError, match="audit"):
        bootstrap_super_admin(
            db=db,
            target_user_id=TARGET_ID,
            offline_approval_reference="CAB-2026-10-12",
            allow_recovery=False,
            correlation_id=CORRELATION_ID,
            audit_writer=NoOpAuditWriter(),
            clock=lambda: NOW,
        )
    assert db.commits == 0
    assert db.rollbacks == 0
