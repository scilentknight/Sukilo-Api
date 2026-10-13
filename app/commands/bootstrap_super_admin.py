"""Bootstrap a verified account as the controlled super administrator."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.transactions import DatabaseTransaction
from app.modules.access.catalog import ROLES
from app.modules.access.models import Role, UserRoleAssignment
from app.modules.access.ports import AccountRepositoryPort, AssignmentRepositoryPort
from app.modules.access.repository import AssignmentRepository
from app.modules.access.types import RoleStatus, ScopeType
from app.modules.identity.repository import AccountRepository
from app.modules.identity.types import AccountStatus
from app.ports.audit import AuditEventInput, AuditWriter, NoOpAuditWriter


SUPER_ADMIN_ROLE_ID = next(
    role.role_id for role in ROLES if role.code == "SUPER_ADMIN"
)


class BootstrapAccount(Protocol):
    """Describe account state required by privileged bootstrap."""

    user_id: UUID
    status: AccountStatus
    email_verified_at: datetime | None
    phone_verified_at: datetime | None
    auth_version: int


class SuperAdminBootstrapError(Exception):
    """Report an invalid or unsafe bootstrap request."""


@dataclass(frozen=True, slots=True)
class SuperAdminBootstrapCommand:
    """Describe one approved offline bootstrap request."""

    target_user_id: UUID
    offline_approval_reference: str
    allow_recovery: bool
    correlation_id: UUID


class SuperAdminBootstrapService:
    """Create a global super-administrator assignment under transaction locks."""

    def __init__(
        self,
        *,
        repository: AssignmentRepositoryPort,
        accounts: AccountRepositoryPort,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize transaction-bound bootstrap dependencies."""
        _require_durable_audit(audit_writer)
        self.repository = repository
        self.accounts = accounts
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock

    def bootstrap(self, command: SuperAdminBootstrapCommand) -> UUID:
        """Stage one approved super-administrator assignment.

        Args:
            command: Verified offline bootstrap request.

        Returns:
            Stable identifier of the staged assignment.

        Raises:
            SuperAdminBootstrapError: If bootstrap safety checks fail.
        """
        approval = command.offline_approval_reference.strip()
        if not approval:
            raise SuperAdminBootstrapError("Offline approval is required.")
        now = self.clock()
        role = self.repository.get_role_by_code_for_update("SUPER_ADMIN")
        _validate_super_admin_role(role)
        self.repository.acquire_bootstrap_lock(role.role_id)
        if self.repository.has_active_role_assignment(role.role_id, now):
            if not command.allow_recovery:
                raise SuperAdminBootstrapError("Active SUPER_ADMIN already exists.")
        account = self.accounts.get_by_id(command.target_user_id, for_update=True)
        _validate_account(account)
        account = cast(BootstrapAccount, account)
        self.repository.acquire_overlap_lock(
            account.user_id,
            role.role_id,
            ScopeType.GLOBAL,
            None,
        )
        if self.repository.has_active_overlap(
            user_id=account.user_id,
            role_id=role.role_id,
            scope_type=ScopeType.GLOBAL,
            scope_id=None,
            valid_from=None,
            valid_to=None,
            now=now,
        ):
            raise SuperAdminBootstrapError("Target SUPER_ADMIN already exists.")
        assignment = _build_assignment(account.user_id, role.role_id, now)
        account.auth_version += 1
        self.repository.add(assignment)
        self.repository.flush()
        self.audit_writer.stage(
            _audit_event(command, assignment.assignment_id, account.user_id),
            self.transaction,
        )
        return assignment.assignment_id


def bootstrap_super_admin(
    *,
    db: Session,
    target_user_id: UUID,
    offline_approval_reference: str,
    allow_recovery: bool,
    correlation_id: UUID,
    audit_writer: AuditWriter,
    clock: Callable[[], datetime] | None = None,
) -> UUID:
    """Run and commit one operational super-administrator bootstrap.

    Args:
        db: Active database session used for the atomic operation.
        target_user_id: Existing account receiving global authority.
        offline_approval_reference: Nonblank offline approval reference.
        allow_recovery: Whether an existing active super admin may be bypassed.
        correlation_id: Operational correlation identifier.
        audit_writer: Transaction-bound durable audit adapter.
        clock: Optional UTC clock.

    Returns:
        Stable identifier of the created assignment.

    Raises:
        SuperAdminBootstrapError: If a bootstrap safety check fails.
    """
    _require_durable_audit(audit_writer)
    transaction = DatabaseTransaction(db)
    service = SuperAdminBootstrapService(
        repository=AssignmentRepository(db),
        accounts=AccountRepository(db),
        audit_writer=audit_writer,
        transaction=transaction,
        clock=clock or _utc_now,
    )
    command = SuperAdminBootstrapCommand(
        target_user_id=target_user_id,
        offline_approval_reference=offline_approval_reference,
        allow_recovery=allow_recovery,
        correlation_id=correlation_id,
    )
    try:
        assignment_id = service.bootstrap(command)
        db.commit()
        return assignment_id
    except Exception:
        db.rollback()
        raise


def _validate_super_admin_role(role: Role | None) -> None:
    if (
        role is None
        or role.role_id != SUPER_ADMIN_ROLE_ID
        or role.code != "SUPER_ADMIN"
        or role.scope_type != ScopeType.GLOBAL
        or role.status != RoleStatus.ACTIVE
    ):
        raise SuperAdminBootstrapError("Deterministic SUPER_ADMIN role is unavailable.")


def _require_durable_audit(audit_writer: AuditWriter) -> None:
    if isinstance(audit_writer, NoOpAuditWriter):
        raise SuperAdminBootstrapError(
            "SUPER_ADMIN bootstrap requires a durable audit writer."
        )


def _validate_account(account: object) -> None:
    if account is None or getattr(account, "status", None) != AccountStatus.ACTIVE:
        raise SuperAdminBootstrapError("Target account must be active.")
    email_verified = getattr(account, "email_verified_at", None) is not None
    phone_verified = getattr(account, "phone_verified_at", None) is not None
    if not email_verified and not phone_verified:
        raise SuperAdminBootstrapError("Target account must be verified.")


def _build_assignment(
    user_id: UUID,
    role_id: UUID,
    now: datetime,
) -> UserRoleAssignment:
    return UserRoleAssignment(
        assignment_id=uuid4(),
        user_id=user_id,
        role_id=role_id,
        scope_type=ScopeType.GLOBAL,
        scope_id=None,
        country_id=None,
        vendor_id=None,
        valid_from=None,
        valid_to=None,
        created_by=user_id,
        created_at=now,
    )


def _audit_event(
    command: SuperAdminBootstrapCommand,
    assignment_id: UUID,
    target_user_id: UUID,
) -> AuditEventInput:
    return AuditEventInput(
        action="iam.super_admin.bootstrapped",
        resource_type="iam.user_role_assignment",
        resource_id=assignment_id,
        correlation_id=command.correlation_id,
        effective_user_id=target_user_id,
        after_data={
            "role_code": "SUPER_ADMIN",
            "scope_type": "GLOBAL",
            "offline_approval_recorded": True,
            "recovery": command.allow_recovery,
        },
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
