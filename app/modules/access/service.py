"""Administer scoped role assignments."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from app.core.auth_context import AuthContext
from app.core.database_context import (
    GLOBAL_ASSIGNMENT_READ_ACTION,
    AuthorizationMode,
    DatabaseRequestContext,
    bind_database_context,
)
from app.core.transactions import DatabaseTransaction
from app.modules.access.models import Permission, Role, UserRoleAssignment
from app.modules.access.policies import (
    AuthorizationRequest,
    PolicyEvaluator,
    ResourceScope,
)
from app.modules.access.ports import (
    AccountRepositoryPort,
    AssignmentRepositoryPort,
    AuthorizationCachePort,
    Clock,
)
from app.modules.access.types import RoleStatus, ScopeType
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.types import AccountStatus, GrantPurpose
from app.ports.audit import AuditEventInput, AuditWriter
from app.ports.country import CountryReader


class AccountState(Protocol):
    """Describe account fields changed by assignment mutations."""

    user_id: UUID
    status: AccountStatus
    auth_version: int


class AssignmentError(Exception):
    """Report a safe assignment operation failure."""


class AssignmentDeniedError(AssignmentError):
    """Report denied assignment administration."""


class AssignmentValidationError(AssignmentError):
    """Report invalid assignment input."""


class AssignmentConflictError(AssignmentError):
    """Report an overlapping active assignment."""


class AssignmentNotFoundError(AssignmentError):
    """Report an unavailable assignment."""


@dataclass(frozen=True, slots=True)
class GrantAssignment:
    """Describe a public assignment grant."""

    actor: AuthContext
    target_user_id: UUID
    role_code: str
    scope: ResourceScope
    valid_from: datetime | None
    valid_to: datetime | None
    step_up_token: str


@dataclass(frozen=True, slots=True)
class RevokeAssignment:
    """Describe a public assignment revocation."""

    actor: AuthContext
    assignment_id: UUID
    reason: str
    step_up_token: str
    country_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AssignmentView:
    """Expose safe assignment state."""

    assignment_id: UUID
    user_id: UUID
    role_id: UUID
    role_code: str
    scope: ResourceScope
    valid_from: datetime | None
    valid_to: datetime | None
    revoked_at: datetime | None
    version: int


@dataclass(frozen=True, slots=True)
class AssignmentQuery:
    """Describe an explicitly filtered assignment query."""

    actor: AuthContext
    user_id: UUID | None
    country_id: UUID | None
    limit: int
    cursor: UUID | None


class AssignmentService:
    """Enforce assignment administration policy."""

    def __init__(
        self,
        *,
        repository: AssignmentRepositoryPort,
        accounts: AccountRepositoryPort,
        grants: AuthGrantService,
        countries: CountryReader,
        policy_evaluator: PolicyEvaluator,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        cache: AuthorizationCachePort,
        clock: Clock,
        events: SecurityEventRecorder | None = None,
    ) -> None:
        """Initialize assignment dependencies."""
        self.repository = repository
        self.accounts = accounts
        self.grants = grants
        self.countries = countries
        self.policy_evaluator = policy_evaluator
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.cache = cache
        self.clock = clock
        self.events = events
        self.pending_invalidations: list[tuple[UUID, int]] = []

    def list_roles(
        self,
        actor: AuthContext,
        country_id: UUID | None = None,
    ) -> list[Role]:
        """List active role definitions visible to the actor."""
        self._require_catalog_read(actor, "iam.role.read", country_id)
        return self.repository.list_active_roles()

    def list_permissions(
        self,
        actor: AuthContext,
        country_id: UUID | None = None,
    ) -> list[Permission]:
        """List active permission definitions visible to the actor."""
        self._require_catalog_read(actor, "iam.permission.read", country_id)
        return self.repository.list_active_permissions()

    def list_assignments(
        self,
        query: AssignmentQuery,
    ) -> list[AssignmentView]:
        """List only explicitly filtered assignments in actor scope."""
        if query.user_id is None and query.country_id is None:
            raise AssignmentValidationError("An assignment filter is required.")
        self._bind_self(query.actor)
        projections = self.repository.list_active_projections(
            query.actor.user_id,
            self.clock(),
        )
        if query.country_id is None:
            scope = ResourceScope(ScopeType.GLOBAL, None, None, None)
        else:
            scope = ResourceScope(
                ScopeType.COUNTRY,
                query.country_id,
                query.country_id,
                None,
            )
        self._require_permission(
            query.actor,
            "iam.assignment.read",
            scope,
            projections,
        )
        self._bind_operator(query.actor, query.country_id, "IAM_ASSIGNMENT_READ")
        rows = self.repository.list_assignments(
            user_id=query.user_id,
            country_id=query.country_id,
            cursor=query.cursor,
            limit=query.limit,
        )
        return self._visible_assignment_views(query.actor, rows, projections)

    def grant_assignment(self, command: GrantAssignment) -> AssignmentView:
        """Grant one scoped role assignment."""
        now = self.clock()
        self._bind_self(command.actor)
        _validate_grant_command(command, now)
        role = self.repository.get_role_by_code_for_update(command.role_code)
        _validate_role(role, command.scope)
        self._validate_country(command.scope)
        projections = self.repository.list_active_projections(
            command.actor.user_id,
            now,
        )
        self._require_permission(
            command.actor,
            "iam.assignment.grant",
            command.scope,
            projections,
        )
        permissions = self.repository.get_role_permissions(role.role_id)
        self._require_delegable(
            command.actor,
            command.scope,
            permissions,
            projections,
        )
        target = self._lock_active_account(command.target_user_id)
        self.repository.acquire_overlap_lock(
            command.target_user_id,
            role.role_id,
            command.scope.scope_type,
            command.scope.scope_id,
        )
        if self.repository.has_active_overlap(
            user_id=command.target_user_id,
            role_id=role.role_id,
            scope_type=command.scope.scope_type,
            scope_id=command.scope.scope_id,
            valid_from=command.valid_from,
            valid_to=command.valid_to,
            now=now,
        ):
            raise AssignmentConflictError("Assignment validity overlaps.")
        self._consume_step_up(command, "iam.assignment.grant")
        self._bind_operator(
            command.actor,
            command.scope.country_id,
            "IAM_ASSIGNMENT_GRANT",
        )
        assignment = _new_assignment(command, role, now)
        previous_version = target.auth_version
        target.auth_version += 1
        self.repository.add(assignment)
        self.repository.flush()
        self._stage_granted(command, assignment, role)
        self.pending_invalidations.append(
            (command.target_user_id, previous_version)
        )
        return _view(assignment, role.code)

    def revoke_assignment(self, command: RevokeAssignment) -> AssignmentView:
        """Revoke one scoped role assignment."""
        reason = command.reason.strip()
        if not reason:
            raise AssignmentValidationError("Revocation reason is required.")
        now = self.clock()
        if command.country_id is None:
            self._bind_self(command.actor)
        else:
            self._bind_operator(
                command.actor,
                command.country_id,
                "IAM_ASSIGNMENT_REVOKE",
            )
        assignment = self.repository.get_assignment_for_update(
            command.assignment_id,
        )
        if assignment is None or assignment.revoked_at is not None:
            raise AssignmentNotFoundError("Assignment is unavailable.")
        role_code = getattr(assignment, "_role_code", "")
        if role_code == "SUPER_ADMIN":
            raise AssignmentDeniedError("SUPER_ADMIN is not publicly managed.")
        scope = _assignment_scope(assignment)
        self._validate_country(scope)
        projections = self.repository.list_active_projections(
            command.actor.user_id,
            now,
        )
        self._require_permission(
            command.actor,
            "iam.assignment.revoke",
            scope,
            projections,
        )
        permissions = self.repository.get_role_permissions(assignment.role_id)
        self._require_delegable(
            command.actor,
            scope,
            permissions,
            projections,
        )
        target = self._lock_active_account(assignment.user_id)
        self._consume_step_up(command, "iam.assignment.revoke")
        previous_version = target.auth_version
        target.auth_version += 1
        assignment.revoked_at = now
        assignment.revoked_by = command.actor.user_id
        assignment.revocation_reason = reason
        assignment.version += 1
        self.repository.flush()
        self._stage_revoked(command, assignment, role_code, scope)
        self.pending_invalidations.append(
            (assignment.user_id, previous_version)
        )
        return _view(assignment, role_code)

    def commit_invalidations(self) -> None:
        """Invalidate staged authorization keys after database commit."""
        pending = tuple(self.pending_invalidations)
        self.pending_invalidations.clear()
        for user_id, auth_version in pending:
            self.cache.invalidate(user_id, auth_version)

    def discard_invalidations(self) -> None:
        """Discard staged invalidations after database rollback."""
        self.pending_invalidations.clear()

    def _bind_self(self, actor: AuthContext) -> None:
        """Let the actor read only their own assignment rows."""
        bind_database_context(
            self.transaction.session,
            DatabaseRequestContext(
                authorization_mode=AuthorizationMode.SELF,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                action_class="ASSIGNMENT_SELF",
                correlation_id=actor.correlation_id,
            ),
        )

    def _bind_operator(
        self,
        actor: AuthContext,
        country_id: UUID | None,
        action_class: str,
    ) -> None:
        """Set operator context from the already-authorized country scope."""
        if country_id is None:
            context = DatabaseRequestContext(
                authorization_mode=AuthorizationMode.GLOBAL_OPERATOR,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                action_class=GLOBAL_ASSIGNMENT_READ_ACTION,
                correlation_id=actor.correlation_id,
            )
        else:
            context = DatabaseRequestContext(
                authorization_mode=AuthorizationMode.COUNTRY_OPERATOR,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                effective_country_id=country_id,
                action_class=action_class,
                correlation_id=actor.correlation_id,
            )
        bind_database_context(self.transaction.session, context)

    def stage_failure_audit(
        self,
        command: GrantAssignment | RevokeAssignment,
        action: str,
    ) -> None:
        """Stage durable redacted evidence after a failed mutation rollback."""
        allowed_actions = {
            "iam.authorization.step_up_failed",
            "iam.assignment.grant_denied",
        }
        if action not in allowed_actions:
            raise ValueError("Assignment failure audit action is invalid.")
        scope = (
            command.scope
            if isinstance(command, GrantAssignment)
            else None
        )
        event = AuditEventInput(
            action=action,
            resource_type="iam.assignment_request",
            resource_id=_command_resource_id(command),
            correlation_id=command.actor.correlation_id,
            actor_user_id=command.actor.user_id,
            country_id=scope.country_id if scope is not None else None,
            vendor_id=scope.vendor_id if scope is not None else None,
            after_data=_failure_audit_data(command),
        )
        self.audit_writer.stage(event, self.transaction)

    def _require_catalog_read(
        self,
        actor: AuthContext,
        permission_code: str,
        country_id: UUID | None,
    ) -> None:
        scope = _catalog_scope(country_id)
        projections = self.repository.list_active_projections(
            actor.user_id,
            self.clock(),
        )
        self._require_permission(actor, permission_code, scope, projections)

    def _visible_assignment_views(
        self,
        actor: AuthContext,
        assignments: list[UserRoleAssignment],
        projections: list[object],
    ) -> list[AssignmentView]:
        visible = []
        for assignment in assignments:
            scope = _assignment_scope(assignment)
            request = AuthorizationRequest(
                actor,
                "iam.assignment.read",
                scope,
                None,
            )
            decision = self.policy_evaluator.evaluate(request, projections)
            if decision.allowed:
                visible.append(
                    _view(assignment, getattr(assignment, "_role_code", ""))
                )
        return visible

    def _validate_country(self, scope: ResourceScope) -> None:
        if scope.scope_type == ScopeType.GLOBAL:
            return
        if scope.country_id is None or not self.countries.is_operation_enabled(
            scope.country_id,
            "iam.assignment.manage",
        ):
            raise AssignmentDeniedError("Assignment country is unavailable.")

    def _lock_active_account(self, user_id: UUID) -> AccountState:
        account = self.accounts.get_by_id(user_id, for_update=True)
        if account is None or account.status != AccountStatus.ACTIVE:
            raise AssignmentDeniedError("Target account is unavailable.")
        return account

    def _require_permission(
        self,
        actor: AuthContext,
        permission: str,
        scope: ResourceScope,
        projections: list[object],
    ) -> None:
        request = AuthorizationRequest(actor, permission, scope, None)
        decision = self.policy_evaluator.evaluate(request, projections)
        if not decision.allowed:
            self._emit("iam.authorization.denied", actor, scope.country_id)
            raise AssignmentDeniedError("Assignment permission is denied.")

    def _require_delegable(
        self,
        actor: AuthContext,
        scope: ResourceScope,
        permissions: frozenset[str],
        projections: list[object],
    ) -> None:
        for permission in permissions:
            request = AuthorizationRequest(actor, permission, scope, None)
            if not self.policy_evaluator.evaluate(request, projections).allowed:
                raise AssignmentDeniedError(
                    "Actor lacks delegated role capability."
                )

    def _consume_step_up(
        self,
        command: GrantAssignment | RevokeAssignment,
        action_class: str,
    ) -> None:
        self.grants.consume(
            command.step_up_token,
            user_id=command.actor.user_id,
            session_id=command.actor.session_id,
            purpose=GrantPurpose.STEP_UP,
            action_class=action_class,
            subject_hash=None,
        )
        event = AuditEventInput(
            action="iam.authorization.step_up_completed",
            resource_type="iam.assignment_request",
            resource_id=_command_resource_id(command),
            correlation_id=command.actor.correlation_id,
            actor_user_id=command.actor.user_id,
            after_data={"action_class": action_class},
        )
        self.audit_writer.stage(event, self.transaction)

    def _stage_granted(
        self,
        command: GrantAssignment,
        assignment: UserRoleAssignment,
        role: Role,
    ) -> None:
        event = AuditEventInput(
            action="iam.assignment.granted",
            resource_type="iam.user_role_assignment",
            resource_id=assignment.assignment_id,
            correlation_id=command.actor.correlation_id,
            actor_user_id=command.actor.user_id,
            country_id=command.scope.country_id,
            vendor_id=command.scope.vendor_id,
            after_data=_audit_data(role.code, command.scope),
        )
        self.audit_writer.stage(event, self.transaction)
        self._emit("iam.assignment.granted", command.actor, command.scope.country_id)

    def _stage_revoked(
        self,
        command: RevokeAssignment,
        assignment: UserRoleAssignment,
        role_code: str,
        scope: ResourceScope,
    ) -> None:
        event = AuditEventInput(
            action="iam.assignment.revoked",
            resource_type="iam.user_role_assignment",
            resource_id=assignment.assignment_id,
            correlation_id=command.actor.correlation_id,
            actor_user_id=command.actor.user_id,
            country_id=scope.country_id,
            vendor_id=scope.vendor_id,
            before_data=_audit_data(role_code, scope),
            after_data={"revoked": True, "reason": command.reason.strip()},
        )
        self.audit_writer.stage(event, self.transaction)
        self._emit("iam.assignment.revoked", command.actor, scope.country_id)

    def _emit(
        self,
        action: str,
        actor: AuthContext,
        country_id: UUID | None,
    ) -> None:
        """Forward one assignment outcome without the revocation reason text."""
        if self.events is None:
            return
        if action.endswith("granted"):
            reason = "GRANTED"
        elif action.endswith("denied"):
            reason = "DENIED"
        else:
            reason = "REVOKED"
        self.events.record_signal(
            action,
            actor.correlation_id,
            country_id=country_id,
            attributes={"reason_code": reason},
        )


def _validate_grant_command(
    command: GrantAssignment,
    now: datetime,
) -> None:
    if command.actor.user_id == command.target_user_id:
        raise AssignmentDeniedError("Public self-grant is forbidden.")
    if command.role_code == "SUPER_ADMIN":
        raise AssignmentDeniedError("SUPER_ADMIN is not publicly managed.")
    if command.valid_to is not None and command.valid_to <= now:
        raise AssignmentValidationError("Assignment validity has ended.")
    if (
        command.valid_from is not None
        and command.valid_to is not None
        and command.valid_to <= command.valid_from
    ):
        raise AssignmentValidationError("Assignment validity is invalid.")


def _validate_role(role: Role | None, scope: ResourceScope) -> None:
    if role is None or role.status != RoleStatus.ACTIVE:
        raise AssignmentValidationError("Assignment role is unavailable.")
    if role.code == "SUPER_ADMIN":
        raise AssignmentDeniedError("SUPER_ADMIN is not publicly managed.")
    if role.scope_type != scope.scope_type:
        raise AssignmentValidationError("Role scope type is invalid.")


def _new_assignment(
    command: GrantAssignment,
    role: Role,
    now: datetime,
) -> UserRoleAssignment:
    return UserRoleAssignment(
        assignment_id=uuid4(),
        user_id=command.target_user_id,
        role_id=role.role_id,
        scope_type=command.scope.scope_type,
        scope_id=command.scope.scope_id,
        country_id=command.scope.country_id,
        vendor_id=command.scope.vendor_id,
        valid_from=command.valid_from,
        valid_to=command.valid_to,
        created_by=command.actor.user_id,
        created_at=now,
    )


def _assignment_scope(assignment: UserRoleAssignment) -> ResourceScope:
    return ResourceScope(
        ScopeType(assignment.scope_type),
        assignment.scope_id,
        assignment.country_id,
        assignment.vendor_id,
    )


def _catalog_scope(country_id: UUID | None) -> ResourceScope:
    if country_id is None:
        return ResourceScope(ScopeType.GLOBAL, None, None, None)
    return ResourceScope(
        ScopeType.COUNTRY,
        country_id,
        country_id,
        None,
    )


def _audit_data(
    role_code: str,
    scope: ResourceScope,
) -> dict[str, object]:
    return {
        "role_code": role_code,
        "scope_type": scope.scope_type.value,
        "scope_id": str(scope.scope_id) if scope.scope_id else None,
        "country_id": str(scope.country_id) if scope.country_id else None,
        "vendor_id": str(scope.vendor_id) if scope.vendor_id else None,
    }


def _command_resource_id(
    command: GrantAssignment | RevokeAssignment,
) -> UUID:
    if isinstance(command, GrantAssignment):
        return command.target_user_id
    return command.assignment_id


def _failure_audit_data(
    command: GrantAssignment | RevokeAssignment,
) -> dict[str, object]:
    if isinstance(command, GrantAssignment):
        return _audit_data(command.role_code, command.scope)
    return {"operation": "revoke"}


def _view(
    assignment: UserRoleAssignment,
    role_code: str,
) -> AssignmentView:
    return AssignmentView(
        assignment_id=assignment.assignment_id,
        user_id=assignment.user_id,
        role_id=assignment.role_id,
        role_code=role_code,
        scope=_assignment_scope(assignment),
        valid_from=assignment.valid_from,
        valid_to=assignment.valid_to,
        revoked_at=assignment.revoked_at,
        version=assignment.version,
    )
