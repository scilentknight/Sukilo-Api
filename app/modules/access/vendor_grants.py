"""Grant and check vendor badges without using the public assignment route."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.modules.access.models import Role, UserRoleAssignment
from app.modules.access.policies import (
    AuthorizationRequest,
    PolicyEvaluator,
    ResourceScope,
)
from app.modules.access.repository import AssignmentRepository
from app.modules.access.types import ScopeType
from app.modules.identity.models import UserAccount


class VendorGrantError(Exception):
    """Report a vendor badge that cannot be stored."""


class VendorAuthorizer(Protocol):
    """Decide whether an actor may review or manage one seller scope."""

    def allows(
        self,
        actor: AuthContext,
        permission: str,
        country_id: UUID,
        vendor_id: UUID | None,
    ) -> bool:
        """Return whether the actor holds the permission in scope."""
        ...


class VendorRoleGranter(Protocol):
    """Change vendor badges inside the caller's transaction."""

    def grant_vendor_owner(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Grant VENDOR_OWNER and return the assignment id."""
        ...

    def grant_vendor_staff(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Grant VENDOR_STAFF and return the assignment id."""
        ...

    def find_active_assignment(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        role_code: str,
    ) -> UUID | None:
        """Return an active assignment id for one vendor role."""
        ...

    def revoke_assignment(
        self,
        assignment_id: UUID,
        actor_user_id: UUID,
        reason: str,
    ) -> None:
        """Revoke one vendor assignment."""
        ...


class AssignmentVendorAuthorizer:
    """Authorize vendor actions from active assignment projections."""

    def __init__(self, db: Session, clock: Callable[[], datetime]) -> None:
        """Initialize the authorizer.

        Args:
            db: Active SQLAlchemy session.
            clock: UTC time source.
        """
        self.repository = AssignmentRepository(db)
        self.evaluator = PolicyEvaluator(clock)
        self.clock = clock

    def allows(
        self,
        actor: AuthContext,
        permission: str,
        country_id: UUID,
        vendor_id: UUID | None,
    ) -> bool:
        """Return whether the actor holds the permission in scope.

        Args:
            actor: Authenticated principal.
            permission: Permission code.
            country_id: Market identifier.
            vendor_id: Organization identifier, when the scope is a vendor.

        Returns:
            Whether an active assignment covers the permission.
        """
        if vendor_id is None:
            scope = ResourceScope(ScopeType.COUNTRY, country_id, country_id, None)
        else:
            scope = ResourceScope(ScopeType.VENDOR, vendor_id, country_id, vendor_id)
        projections = self.repository.list_active_projections(actor.user_id, self.clock())
        decision = self.evaluator.evaluate(
            AuthorizationRequest(actor, permission, scope, None),
            projections,
        )
        return decision.allowed


class SessionVendorRoleGranter:
    """Insert vendor role assignments on the current session."""

    def __init__(self, db: Session) -> None:
        """Initialize the granter.

        Args:
            db: Active SQLAlchemy session.
        """
        self.db = db

    def grant_vendor_owner(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Grant VENDOR_OWNER and return the assignment id."""
        return self._grant("VENDOR_OWNER", user_id, vendor_id, country_id, actor_user_id)

    def grant_vendor_staff(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Grant VENDOR_STAFF and return the assignment id."""
        return self._grant("VENDOR_STAFF", user_id, vendor_id, country_id, actor_user_id)

    def revoke_assignment(
        self,
        assignment_id: UUID,
        actor_user_id: UUID,
        reason: str,
    ) -> None:
        """Revoke one vendor assignment and force a new login.

        Args:
            assignment_id: Assignment to revoke.
            actor_user_id: Account performing the revocation.
            reason: Non-empty revocation reason.
        """
        assignment = self.db.scalar(
            select(UserRoleAssignment)
            .where(UserRoleAssignment.assignment_id == assignment_id)
            .with_for_update()
        )
        if assignment is None or assignment.revoked_at is not None:
            raise VendorGrantError("Assignment is unavailable.")
        account = self._lock_account(assignment.user_id)
        account.auth_version += 1
        assignment.revoked_at = datetime.now(UTC)
        assignment.revoked_by = actor_user_id
        assignment.revocation_reason = reason
        assignment.version += 1
        self.db.flush()

    def find_active_assignment(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        role_code: str,
    ) -> UUID | None:
        """Return an active assignment id for one vendor role.

        Args:
            user_id: Account identifier.
            vendor_id: Organization identifier.
            country_id: Market identifier.
            role_code: Role code to match.

        Returns:
            Assignment id, or `None`.
        """
        role = self._role(role_code)
        statement = select(UserRoleAssignment.assignment_id).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.vendor_id == vendor_id,
            UserRoleAssignment.country_id == country_id,
            UserRoleAssignment.role_id == role.role_id,
            UserRoleAssignment.revoked_at.is_(None),
        )
        return self.db.scalar(statement)

    def _grant(
        self,
        role_code: str,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Insert one vendor-scoped assignment and bump auth_version."""
        role = self._role(role_code)
        account = self._lock_account(user_id)
        account.auth_version += 1
        assignment_id = uuid4()
        self.db.add(
            UserRoleAssignment(
                assignment_id=assignment_id,
                user_id=user_id,
                role_id=role.role_id,
                scope_type=ScopeType.VENDOR,
                scope_id=vendor_id,
                country_id=country_id,
                vendor_id=vendor_id,
                created_by=actor_user_id,
                version=1,
            )
        )
        self.db.flush()
        return assignment_id

    def _role(self, code: str) -> Role:
        """Return one seeded role or fail closed."""
        role = self.db.scalar(select(Role).where(Role.code == code))
        if role is None:
            raise VendorGrantError("Vendor role is not seeded.")
        return role

    def _lock_account(self, user_id: UUID) -> UserAccount:
        """Lock one account before changing its auth version."""
        account = self.db.scalar(
            select(UserAccount).where(UserAccount.user_id == user_id).with_for_update()
        )
        if account is None:
            raise VendorGrantError("Account is unavailable.")
        return account
