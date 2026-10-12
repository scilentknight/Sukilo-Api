"""Expose thin scoped access-control administration routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.application import AccessApplication
from app.modules.access.dependencies import (
    get_access_application,
    require_authenticated,
    require_recent_step_up,
)
from app.modules.access.policies import ResourceScope, ScopeValidationError
from app.modules.access.schemas import (
    AssignmentResponse,
    GrantAssignmentRequest,
    PermissionResponse,
    RevokeAssignmentRequest,
    RoleResponse,
)
from app.modules.access.service import (
    AssignmentConflictError,
    AssignmentDeniedError,
    AssignmentNotFoundError,
    AssignmentQuery,
    AssignmentService,
    AssignmentValidationError,
    AssignmentView,
    GrantAssignment,
    RevokeAssignment,
)
from app.modules.identity.grants import AuthGrantError


router = APIRouter(prefix="/access", tags=["Access"])


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[
        AccessApplication,
        Depends(get_access_application),
    ],
    country_id: Annotated[UUID | None, Query()] = None,
) -> list[object]:
    """List active roles visible to the authenticated administrator."""
    try:
        return application.assignments.list_roles(context, country_id)
    except AssignmentDeniedError as exc:
        raise _forbidden() from exc


@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[
        AccessApplication,
        Depends(get_access_application),
    ],
    country_id: Annotated[UUID | None, Query()] = None,
) -> list[object]:
    """List active permissions visible to the administrator."""
    try:
        return application.assignments.list_permissions(context, country_id)
    except AssignmentDeniedError as exc:
        raise _forbidden() from exc


@router.get("/assignments", response_model=list[AssignmentResponse])
def list_assignments(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[
        AccessApplication,
        Depends(get_access_application),
    ],
    user_id: Annotated[UUID | None, Query()] = None,
    country_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> list[AssignmentResponse]:
    """List assignments using explicit user and/or country filters."""
    if user_id is None and country_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An explicit assignment filter is required.",
        )
    query = AssignmentQuery(context, user_id, country_id, limit, cursor)
    try:
        rows = application.assignments.list_assignments(query)
        return [_assignment_response(row) for row in rows]
    except AssignmentDeniedError as exc:
        raise _forbidden() from exc


@router.post(
    "/assignments",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def grant_assignment(
    payload: GrantAssignmentRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[
        AccessApplication,
        Depends(get_access_application),
    ],
) -> AssignmentResponse:
    """Grant one assignment and invalidate authorization after commit."""
    command = None
    try:
        scope = ResourceScope(
            payload.scope_type,
            payload.scope_id,
            payload.country_id,
            payload.vendor_id,
        )
        command = GrantAssignment(
            actor=context,
            target_user_id=payload.user_id,
            role_code=payload.role_code,
            scope=scope,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            step_up_token=step_up_token,
        )
        view = application.assignments.grant_assignment(command)
        db.commit()
        application.assignments.commit_invalidations()
        return _assignment_response(view)
    except Exception as exc:
        _handle_mutation_failure(
            db,
            application.assignments,
            command,
            exc,
        )
        _raise_assignment_error(exc)


@router.delete(
    "/assignments/{assignment_id}",
    response_model=AssignmentResponse,
)
def revoke_assignment(
    assignment_id: UUID,
    payload: RevokeAssignmentRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    application: Annotated[
        AccessApplication,
        Depends(get_access_application),
    ],
) -> AssignmentResponse:
    """Revoke a locked assignment and invalidate only after commit."""
    command = None
    try:
        command = RevokeAssignment(
            actor=context,
            assignment_id=assignment_id,
            reason=payload.reason,
            step_up_token=step_up_token,
        )
        view = application.assignments.revoke_assignment(command)
        db.commit()
        application.assignments.commit_invalidations()
        return _assignment_response(view)
    except Exception as exc:
        _handle_mutation_failure(
            db,
            application.assignments,
            command,
            exc,
        )
        _raise_assignment_error(exc)


def _assignment_response(view: AssignmentView) -> AssignmentResponse:
    return AssignmentResponse(
        assignment_id=view.assignment_id,
        user_id=view.user_id,
        role_id=view.role_id,
        role_code=view.role_code,
        scope_type=view.scope.scope_type,
        scope_id=view.scope.scope_id,
        country_id=view.scope.country_id,
        vendor_id=view.scope.vendor_id,
        valid_from=view.valid_from,
        valid_to=view.valid_to,
        revoked_at=view.revoked_at,
        version=view.version,
    )


def _handle_mutation_failure(
    db: Session,
    assignments: AssignmentService,
    command: GrantAssignment | RevokeAssignment | None,
    error: Exception,
) -> None:
    db.rollback()
    assignments.discard_invalidations()
    action = _failure_audit_action(error)
    if action is None or command is None:
        return
    try:
        assignments.stage_failure_audit(command, action)
        db.commit()
    except Exception:
        db.rollback()
        raise


def _failure_audit_action(error: Exception) -> str | None:
    if isinstance(error, AuthGrantError):
        return "iam.authorization.step_up_failed"
    if isinstance(error, AssignmentDeniedError):
        return "iam.assignment.grant_denied"
    return None


def _raise_assignment_error(error: Exception) -> None:
    if isinstance(error, AssignmentNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignment is unavailable.",
        ) from error
    if isinstance(error, (AssignmentConflictError, IntegrityError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment conflicts with current state.",
        ) from error
    if isinstance(error, (AssignmentDeniedError, AuthGrantError)):
        raise _forbidden() from error
    if isinstance(error, (AssignmentValidationError, ScopeValidationError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Assignment request is invalid.",
        ) from error
    raise error


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access is denied.",
    )
