"""Test scoped assignment administration HTTP contracts."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.access import get_access_application, router
from app.api.v1.endpoints.auth import get_auth_context
from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.policies import ResourceScope
from app.modules.access.service import (
    AssignmentConflictError,
    AssignmentDeniedError,
    AssignmentView,
)
from app.modules.access.types import ScopeType
from app.modules.identity.grants import AuthGrantError


ACTOR_ID = UUID(int=1)
TARGET_ID = UUID(int=2)
SESSION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)
ROLE_ID = UUID(int=5)
ASSIGNMENT_ID = UUID(int=6)
NOW = datetime(2026, 10, 12, tzinfo=UTC)


class DatabaseFake:
    """Record route transaction boundaries."""

    def __init__(self, fail_commit: bool = False) -> None:
        """Initialize transaction state."""
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0
        self.events = []

    def commit(self) -> None:
        """Commit unless configured to fail."""
        self.commits += 1
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self) -> None:
        """Record a rollback."""
        self.rollbacks += 1
        self.events.append("rollback")


class AssignmentsFake:
    """Record assignment API use cases."""

    def __init__(
        self,
        *,
        conflict: bool = False,
        failure: Exception | None = None,
        audit_failure: bool = False,
    ) -> None:
        """Initialize fake assignment behavior."""
        self.conflict = conflict
        self.failure = failure
        self.audit_failure = audit_failure
        self.queries = []
        self.grants = []
        self.failure_audits = []
        self.invalidations = 0
        self.discards = 0
        self.catalog_scopes = []

    def list_roles(self, actor, country_id=None):
        """Record role catalog authorization scope."""
        self.catalog_scopes.append(("roles", actor, country_id))
        return []

    def list_permissions(self, actor, country_id=None):
        """Record permission catalog authorization scope."""
        self.catalog_scopes.append(("permissions", actor, country_id))
        return []

    def list_assignments(self, query):
        """Return one scoped assignment."""
        self.queries.append(query)
        return [_view()]

    def grant_assignment(self, command):
        """Return a grant or raise a stable conflict."""
        self.grants.append(command)
        if self.conflict:
            raise AssignmentConflictError("overlap")
        if self.failure is not None:
            raise self.failure
        return _view()

    def stage_failure_audit(self, command, action: str) -> None:
        """Record durable post-rollback failure evidence."""
        self.failure_audits.append((command, action))
        if self.audit_failure:
            raise RuntimeError("audit failed")

    def commit_invalidations(self) -> None:
        """Record post-commit invalidation."""
        self.invalidations += 1

    def discard_invalidations(self) -> None:
        """Record rollback cleanup."""
        self.discards += 1


class ApplicationFake:
    """Expose fake assignment use cases."""

    def __init__(self, assignments: AssignmentsFake) -> None:
        """Initialize application services."""
        self.assignments = assignments


def _context() -> AuthContext:
    return AuthContext(
        user_id=ACTOR_ID,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("password", "otp"),
        authenticated_at=NOW,
        correlation_id=UUID(int=7),
    )


def _view() -> AssignmentView:
    return AssignmentView(
        assignment_id=ASSIGNMENT_ID,
        user_id=TARGET_ID,
        role_id=ROLE_ID,
        role_code="COUNTRY_OPERATIONS",
        scope=ResourceScope(
            ScopeType.COUNTRY,
            COUNTRY_ID,
            COUNTRY_ID,
            None,
        ),
        valid_from=None,
        valid_to=None,
        revoked_at=None,
        version=1,
    )


def _client(
    *,
    conflict: bool = False,
    fail_commit: bool = False,
    failure: Exception | None = None,
    audit_failure: bool = False,
) -> tuple[TestClient, AssignmentsFake, DatabaseFake]:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    assignments = AssignmentsFake(
        conflict=conflict,
        failure=failure,
        audit_failure=audit_failure,
    )
    database = DatabaseFake(fail_commit=fail_commit)
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_auth_context] = _context
    app.dependency_overrides[get_access_application] = (
        lambda: ApplicationFake(assignments)
    )
    return TestClient(app, raise_server_exceptions=False), assignments, database


def test_assignment_list_requires_explicit_filter_and_passes_trusted_query() -> None:
    """Reject unscoped lists and pass explicit filters to the service."""
    client, assignments, _ = _client()
    assert client.get("/api/v1/access/assignments").status_code == 422
    response = client.get(
        "/api/v1/access/assignments",
        params={"country_id": str(COUNTRY_ID)},
    )
    assert response.status_code == 200
    assert assignments.queries[0].country_id == COUNTRY_ID
    assert assignments.queries[0].actor.user_id == ACTOR_ID
    assert assignments.queries[0].limit == 50
    assert assignments.queries[0].cursor is None


def test_assignment_list_passes_bounded_cursor_pagination() -> None:
    """Validate list bounds and carry the opaque UUID cursor."""
    client, assignments, _ = _client()
    cursor = UUID(int=30)
    response = client.get(
        "/api/v1/access/assignments",
        params={
            "user_id": str(TARGET_ID),
            "limit": 100,
            "cursor": str(cursor),
        },
    )
    assert response.status_code == 200
    assert assignments.queries[0].limit == 100
    assert assignments.queries[0].cursor == cursor
    assert client.get(
        "/api/v1/access/assignments",
        params={"user_id": str(TARGET_ID), "limit": 0},
    ).status_code == 422
    assert client.get(
        "/api/v1/access/assignments",
        params={"user_id": str(TARGET_ID), "limit": 101},
    ).status_code == 422


def test_catalog_routes_pass_optional_explicit_country() -> None:
    """Pass an explicit catalog country to scoped authorization."""
    client, assignments, _ = _client()
    roles = client.get(
        "/api/v1/access/roles",
        params={"country_id": str(COUNTRY_ID)},
    )
    permissions = client.get(
        "/api/v1/access/permissions",
        params={"country_id": str(COUNTRY_ID)},
    )
    assert roles.status_code == 200
    assert permissions.status_code == 200
    assert [call[2] for call in assignments.catalog_scopes] == [
        COUNTRY_ID,
        COUNTRY_ID,
    ]


def test_grant_uses_step_up_header_and_invalidates_only_after_commit() -> None:
    """Consume header step-up and invalidate authorization after commit."""
    client, assignments, database = _client()
    response = client.post(
        "/api/v1/access/assignments",
        headers={"X-Step-Up-Grant": "grant-token"},
        json={
            "user_id": str(TARGET_ID),
            "role_code": "COUNTRY_OPERATIONS",
            "scope_type": "COUNTRY",
            "scope_id": str(COUNTRY_ID),
            "country_id": str(COUNTRY_ID),
            "vendor_id": None,
            "valid_from": None,
            "valid_to": None,
        },
    )
    assert response.status_code == 201
    assert assignments.grants[0].step_up_token == "grant-token"
    assert database.commits == 1
    assert assignments.invalidations == 1
    assert assignments.discards == 0


def test_failed_mutation_rolls_back_without_cache_invalidation() -> None:
    """Rollback conflicts without invalidating current authorization."""
    client, assignments, database = _client(conflict=True)
    response = client.post(
        "/api/v1/access/assignments",
        headers={"X-Step-Up-Grant": "grant-token"},
        json={
            "user_id": str(TARGET_ID),
            "role_code": "COUNTRY_OPERATIONS",
            "scope_type": "COUNTRY",
            "scope_id": str(COUNTRY_ID),
            "country_id": str(COUNTRY_ID),
        },
    )
    assert response.status_code == 409
    assert database.rollbacks == 1
    assert assignments.invalidations == 0
    assert assignments.discards == 1


def test_denial_audit_occurs_after_rollback_then_commits_fresh_transaction() -> None:
    """Persist a redacted grant denial only after mutation rollback."""
    client, assignments, database = _client(
        failure=AssignmentDeniedError("denied"),
    )
    response = client.post(
        "/api/v1/access/assignments",
        headers={"X-Step-Up-Grant": "secret-step-up-token"},
        json={
            "user_id": str(TARGET_ID),
            "role_code": "COUNTRY_OPERATIONS",
            "scope_type": "COUNTRY",
            "scope_id": str(COUNTRY_ID),
            "country_id": str(COUNTRY_ID),
        },
    )
    assert response.status_code == 403
    assert database.events == ["rollback", "commit"]
    assert assignments.failure_audits[0][1] == "iam.assignment.grant_denied"
    assert "secret-step-up-token" not in repr(assignments.failure_audits[0][1])


def test_step_up_failure_is_durably_audited_after_rollback() -> None:
    """Persist safe failed step-up evidence in a fresh transaction."""
    client, assignments, database = _client(
        failure=AuthGrantError("unavailable"),
    )
    response = client.post(
        "/api/v1/access/assignments",
        headers={"X-Step-Up-Grant": "secret-step-up-token"},
        json={
            "user_id": str(TARGET_ID),
            "role_code": "COUNTRY_OPERATIONS",
            "scope_type": "COUNTRY",
            "scope_id": str(COUNTRY_ID),
            "country_id": str(COUNTRY_ID),
        },
    )
    assert response.status_code == 403
    assert database.events == ["rollback", "commit"]
    assert assignments.failure_audits[0][1] == (
        "iam.authorization.step_up_failed"
    )


def test_audit_failure_never_commits_rolled_back_mutation_state() -> None:
    """Rollback again when durable denial auditing itself fails."""
    client, assignments, database = _client(
        failure=AssignmentDeniedError("denied"),
        audit_failure=True,
    )
    response = client.post(
        "/api/v1/access/assignments",
        headers={"X-Step-Up-Grant": "secret-step-up-token"},
        json={
            "user_id": str(TARGET_ID),
            "role_code": "COUNTRY_OPERATIONS",
            "scope_type": "COUNTRY",
            "scope_id": str(COUNTRY_ID),
            "country_id": str(COUNTRY_ID),
        },
    )
    assert response.status_code == 500
    assert database.events == ["rollback", "rollback"]
    assert database.commits == 0
    assert assignments.invalidations == 0


def test_missing_step_up_header_is_rejected() -> None:
    """Require explicit step-up proof for assignment mutation."""
    client, _, _ = _client()
    response = client.post(
        "/api/v1/access/assignments",
        json={
            "user_id": str(TARGET_ID),
            "role_code": "COUNTRY_OPERATIONS",
            "scope_type": "COUNTRY",
            "scope_id": str(COUNTRY_ID),
            "country_id": str(COUNTRY_ID),
        },
    )
    assert response.status_code == 422


def test_revoke_missing_step_up_header_is_rejected() -> None:
    """Require explicit step-up proof for assignment revocation."""
    client, _, _ = _client()
    response = client.request(
        "DELETE",
        f"/api/v1/access/assignments/{ASSIGNMENT_ID}",
        json={"reason": "No longer required"},
    )
    assert response.status_code == 422
