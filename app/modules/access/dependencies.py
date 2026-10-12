"""Provide composable FastAPI access-control dependencies."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_auth_context
from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.application import (
    AccessApplication,
    AuthorizationDeniedError,
)
from app.modules.access.policies import ResourceScope


ScopeResolver = Callable[[Request], ResourceScope]


def require_recent_step_up(
    step_up_token: Annotated[str, Header(alias="X-Step-Up-Grant")],
) -> str:
    """Require and return an opaque recent step-up grant.

    Args:
        step_up_token: Opaque grant supplied by the protected caller.

    Returns:
        The opaque grant for atomic consumption by the application service.
    """
    return step_up_token


def get_access_application(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> AccessApplication:
    """Build request-scoped access services from application composition."""
    factory = getattr(request.app.state, "access_application_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Access services are unavailable.",
        )
    return factory(db)


def require_authenticated(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Return the validated identity authentication context."""
    return context


def require_permission(
    permission_code: str,
    scope_resolver: ScopeResolver,
) -> Callable[..., AuthContext]:
    """Build a dependency enforcing permission against trusted route scope."""
    def dependency(
        request: Request,
        context: Annotated[AuthContext, Depends(require_authenticated)],
        application: Annotated[
            AccessApplication,
            Depends(get_access_application),
        ],
    ) -> AuthContext:
        """Authorize one request using a server-resolved resource scope."""
        try:
            scope = scope_resolver(request)
            application.authorizations.require_permission(
                context,
                permission_code,
                scope,
            )
            return context
        except AuthorizationDeniedError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access is denied.",
            ) from exc
    return dependency
