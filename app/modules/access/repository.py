"""Persist scoped authorization assignments."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.modules.access.models import (
    Permission,
    Role,
    RolePermission,
    UserRoleAssignment,
)
from app.modules.access.policies import AssignmentProjection, ResourceScope
from app.modules.access.types import PermissionStatus, RoleStatus, ScopeType


class AssignmentRepository:
    """Provide parameterized assignment persistence operations."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository."""
        self.db = db

    def acquire_overlap_lock(
        self,
        user_id: UUID,
        role_id: UUID,
        scope_type: ScopeType,
        scope_id: UUID | None,
    ) -> None:
        """Acquire a transaction lock for one assignment identity."""
        lock_identity = ":".join(
            (
                str(user_id),
                str(role_id),
                scope_type.value,
                str(scope_id) if scope_id is not None else "GLOBAL",
            )
        )
        statement = text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:lock_identity, 0))"
        ).bindparams(lock_identity=lock_identity)
        self.db.execute(statement)

    def acquire_bootstrap_lock(self, role_id: UUID) -> None:
        """Serialize bootstrap decisions for one privileged role."""
        statement = text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:lock_identity, 0))"
        ).bindparams(lock_identity=f"bootstrap-role:{role_id}")
        self.db.execute(statement)

    def has_active_role_assignment(
        self,
        role_id: UUID,
        now: datetime,
    ) -> bool:
        """Return whether any assignment currently activates one role."""
        statement = select(UserRoleAssignment.assignment_id).where(
            UserRoleAssignment.role_id == role_id,
            UserRoleAssignment.revoked_at.is_(None),
            or_(
                UserRoleAssignment.valid_from.is_(None),
                UserRoleAssignment.valid_from <= now,
            ),
            or_(
                UserRoleAssignment.valid_to.is_(None),
                UserRoleAssignment.valid_to > now,
            ),
        ).limit(1)
        return self.db.scalar(statement) is not None

    def has_active_overlap(
        self,
        *,
        user_id: UUID,
        role_id: UUID,
        scope_type: ScopeType,
        scope_id: UUID | None,
        valid_from: datetime | None,
        valid_to: datetime | None,
        now: datetime,
    ) -> bool:
        """Return whether an active validity window overlaps."""
        scope_clause = (
            UserRoleAssignment.scope_id.is_(None)
            if scope_id is None
            else UserRoleAssignment.scope_id == scope_id
        )
        conditions = [
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == role_id,
            UserRoleAssignment.scope_type == scope_type,
            scope_clause,
            UserRoleAssignment.revoked_at.is_(None),
            or_(
                UserRoleAssignment.valid_to.is_(None),
                UserRoleAssignment.valid_to > now,
            ),
        ]
        if valid_from is not None:
            conditions.append(
                or_(
                    UserRoleAssignment.valid_to.is_(None),
                    UserRoleAssignment.valid_to > valid_from,
                )
            )
        if valid_to is not None:
            conditions.append(
                or_(
                    UserRoleAssignment.valid_from.is_(None),
                    UserRoleAssignment.valid_from < valid_to,
                )
            )
        statement = select(UserRoleAssignment.assignment_id).where(*conditions).limit(1)
        return self.db.scalar(statement) is not None

    def list_active_projections(
        self,
        user_id: UUID,
        now: datetime,
    ) -> list[AssignmentProjection]:
        """Resolve active assignment permission projections."""
        statement = (
            select(UserRoleAssignment, Permission.code)
            .join(Role, Role.role_id == UserRoleAssignment.role_id)
            .join(
                RolePermission,
                RolePermission.role_id == UserRoleAssignment.role_id,
            )
            .join(
                Permission,
                Permission.permission_id == RolePermission.permission_id,
            )
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.revoked_at.is_(None),
                or_(
                    UserRoleAssignment.valid_from.is_(None),
                    UserRoleAssignment.valid_from <= now,
                ),
                or_(
                    UserRoleAssignment.valid_to.is_(None),
                    UserRoleAssignment.valid_to > now,
                ),
                Role.status == RoleStatus.ACTIVE,
                Permission.status == PermissionStatus.ACTIVE,
            )
            .order_by(UserRoleAssignment.assignment_id)
        )
        rows = self.db.execute(statement).all()
        return _build_projections(rows)

    def list_active_role_codes(self, user_id: UUID, now: datetime) -> list[str]:
        """Return distinct active role codes for one account."""
        statement = (
            select(Role.code)
            .join(
                UserRoleAssignment,
                UserRoleAssignment.role_id == Role.role_id,
            )
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.revoked_at.is_(None),
                or_(
                    UserRoleAssignment.valid_from.is_(None),
                    UserRoleAssignment.valid_from <= now,
                ),
                or_(
                    UserRoleAssignment.valid_to.is_(None),
                    UserRoleAssignment.valid_to > now,
                ),
                Role.status == RoleStatus.ACTIVE,
            )
            .distinct()
            .order_by(Role.code)
        )
        return list(self.db.scalars(statement))

    def get_role_by_code_for_update(self, code: str) -> Role | None:
        """Lock and return a role by stable code."""
        statement = select(Role).where(Role.code == code).with_for_update()
        return self.db.scalar(statement)

    def list_active_roles(self) -> list[Role]:
        """Return active roles in stable code order."""
        statement = (
            select(Role)
            .where(Role.status == RoleStatus.ACTIVE)
            .order_by(Role.code)
        )
        return list(self.db.scalars(statement))

    def list_active_permissions(self) -> list[Permission]:
        """Return active permissions in stable code order."""
        statement = (
            select(Permission)
            .where(Permission.status == PermissionStatus.ACTIVE)
            .order_by(Permission.code)
        )
        return list(self.db.scalars(statement))

    def list_assignments(
        self,
        *,
        user_id: UUID | None,
        country_id: UUID | None,
        cursor: UUID | None,
        limit: int,
    ) -> list[UserRoleAssignment]:
        """Return a bounded assignment page with database-level filters."""
        if user_id is None and country_id is None:
            raise ValueError("An assignment filter is required.")
        if not 1 <= limit <= 100:
            raise ValueError("Assignment page limit is invalid.")
        statement = (
            select(UserRoleAssignment, Role.code)
            .join(Role, Role.role_id == UserRoleAssignment.role_id)
            .order_by(UserRoleAssignment.assignment_id)
            .limit(limit)
        )
        if user_id is not None:
            statement = statement.where(UserRoleAssignment.user_id == user_id)
        if country_id is not None:
            statement = statement.where(
                UserRoleAssignment.country_id == country_id
            )
        if cursor is not None:
            statement = statement.where(
                UserRoleAssignment.assignment_id > cursor
            )
        rows = self.db.execute(statement).all()
        assignments = []
        for assignment, role_code in rows:
            assignment._role_code = role_code
            assignments.append(assignment)
        return assignments

    def get_role_permissions(self, role_id: UUID) -> frozenset[str]:
        """Return active permission codes mapped to one role."""
        statement = (
            select(Permission.code)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.permission_id,
            )
            .where(
                RolePermission.role_id == role_id,
                Permission.status == PermissionStatus.ACTIVE,
            )
        )
        return frozenset(self.db.scalars(statement))

    def get_assignment_for_update(
        self,
        assignment_id: UUID,
    ) -> UserRoleAssignment | None:
        """Lock and return an assignment with its stable role code."""
        statement = (
            select(UserRoleAssignment, Role.code)
            .join(Role, Role.role_id == UserRoleAssignment.role_id)
            .where(UserRoleAssignment.assignment_id == assignment_id)
            .with_for_update()
        )
        row = self.db.execute(statement).first()
        if row is None:
            return None
        assignment, role_code = row
        assignment._role_code = role_code
        return assignment

    def add(self, assignment: UserRoleAssignment) -> None:
        """Stage an assignment in the active transaction."""
        self.db.add(assignment)

    def flush(self) -> None:
        """Flush staged assignment changes without committing."""
        self.db.flush()


def _build_projections(rows: list[object]) -> list[AssignmentProjection]:
    grouped: dict[UUID, tuple[UserRoleAssignment, set[str]]] = {}
    for assignment, permission_code in rows:
        current = grouped.setdefault(
            assignment.assignment_id,
            (assignment, set()),
        )
        current[1].add(permission_code)
    return [
        AssignmentProjection(
            assignment_id=assignment.assignment_id,
            scope=ResourceScope(
                scope_type=ScopeType(assignment.scope_type),
                scope_id=assignment.scope_id,
                country_id=assignment.country_id,
                vendor_id=assignment.vendor_id,
            ),
            permissions=frozenset(permissions),
            valid_from=assignment.valid_from,
            valid_to=assignment.valid_to,
            revoked_at=assignment.revoked_at,
        )
        for assignment, permissions in grouped.values()
    ]
