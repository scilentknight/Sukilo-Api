"""Test reusable access authorization dependencies."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.endpoints.auth import get_auth_context
from app.core.auth_context import AuthContext
from app.core.config import Settings
from app.modules.access.bootstrap import build_access_application_factory
from app.modules.access.dependencies import (
    get_access_application,
    require_permission,
    require_recent_step_up,
)
from app.modules.access.application import AuthorizationDeniedError
from app.modules.access.policies import ResourceScope
from app.modules.access.types import ScopeType
from app.modules.identity.bootstrap import build_identity_application_factory


USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
COUNTRY_ID = UUID(int=3)


class AuthorizationsFake:
    """Record permission checks."""

    def __init__(self, allowed: bool) -> None:
        """Initialize the authorization outcome."""
        self.allowed = allowed
        self.calls: list[tuple[AuthContext, str, ResourceScope]] = []

    def require_permission(
        self,
        context: AuthContext,
        permission_code: str,
        scope: ResourceScope,
    ) -> None:
        """Record and enforce one permission check."""
        self.calls.append((context, permission_code, scope))
        if not self.allowed:
            raise AuthorizationDeniedError("denied")


class ApplicationFake:
    """Expose fake authorization use cases."""

    def __init__(self, allowed: bool) -> None:
        """Initialize fake application state."""
        self.authorizations = AuthorizationsFake(allowed)


def _context() -> AuthContext:
    return AuthContext(
        user_id=USER_ID,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("password",),
        authenticated_at=datetime(2026, 10, 12, tzinfo=UTC),
        correlation_id=UUID(int=4),
    )


def _country_scope(request: Request) -> ResourceScope:
    country_id = UUID(request.query_params["country_id"])
    return ResourceScope(ScopeType.COUNTRY, country_id, country_id, None)


def _unauthenticated() -> AuthContext:
    raise HTTPException(status_code=401, detail="invalid")


def _client(*, authenticated: bool = True, allowed: bool = True) -> tuple[TestClient, ApplicationFake]:
    app = FastAPI()
    application = ApplicationFake(allowed)

    @app.get("/guard")
    def guarded(
        context: AuthContext = Depends(
            require_permission("iam.assignment.read", _country_scope)
        ),
    ) -> dict[str, str]:
        """Return the authorized principal."""
        return {"user_id": str(context.user_id)}

    app.dependency_overrides[get_access_application] = lambda: application
    if authenticated:
        app.dependency_overrides[get_auth_context] = _context
    else:
        app.dependency_overrides[get_auth_context] = _unauthenticated
    return TestClient(app), application


def test_permission_dependency_uses_server_resolved_scope() -> None:
    """Authorize with scope derived from trusted request context."""
    client, application = _client()
    response = client.get("/guard", params={"country_id": str(COUNTRY_ID)})
    assert response.status_code == 200
    _, permission, scope = application.authorizations.calls[0]
    assert permission == "iam.assignment.read"
    assert scope.country_id == COUNTRY_ID


def test_permission_dependency_maps_missing_auth_and_denial_stably() -> None:
    """Map authentication and permission failures to stable statuses."""
    unauthenticated, _ = _client(authenticated=False)
    denied, _ = _client(allowed=False)
    assert unauthenticated.get(
        "/guard",
        params={"country_id": str(COUNTRY_ID)},
    ).status_code == 401
    assert denied.get(
        "/guard",
        params={"country_id": str(COUNTRY_ID)},
    ).status_code == 403


def test_recent_step_up_dependency_requires_opaque_header() -> None:
    """Return the opaque grant while rejecting a missing header."""
    app = FastAPI()

    @app.post("/sensitive")
    def sensitive(
        grant: str = Depends(require_recent_step_up),
    ) -> dict[str, str]:
        """Return the accepted opaque grant for the protected service."""
        return {"grant": grant}

    client = TestClient(app)
    assert client.post("/sensitive").status_code == 422
    response = client.post(
        "/sensitive",
        headers={"X-Step-Up-Grant": "opaque-grant"},
    )
    assert response.json() == {"grant": "opaque-grant"}


def test_access_composition_reuses_identity_security_adapters() -> None:
    """Reuse exact identity digest, country, audit, and clock instances."""
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
    )
    identity = build_identity_application_factory(settings)
    access = build_access_application_factory(settings, identity)
    assert access.digests is identity.digests
    assert access.countries is identity.countries
    assert access.audit_writer is identity.audit_writer
    assert access.clock is identity.clock


def test_production_access_rejects_development_noop_audit() -> None:
    """Refuse production assignment APIs without durable audit writing."""
    development = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
    )
    production = development.model_copy(
        update={"APP_ENV": "production", "REDIS_URL": "redis://localhost"},
    )
    identity = build_identity_application_factory(development)
    with pytest.raises(ValueError, match="audit writer"):
        build_access_application_factory(production, identity)
