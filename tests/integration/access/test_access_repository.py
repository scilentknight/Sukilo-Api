"""Test PostgreSQL assignment repository statements."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from sqlalchemy.dialects import postgresql

from app.modules.access.repository import AssignmentRepository
from app.modules.access.types import ScopeType


NOW = datetime(2026, 10, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
ROLE_ID = UUID(int=2)
COUNTRY_ID = UUID(int=3)
CURSOR_ID = UUID(int=4)


def _compiled(statement) -> tuple[str, dict[str, object]]:
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_overlap_lock_uses_postgresql_transaction_advisory_lock() -> None:
    """Serialize equal assignment identities inside the transaction."""
    db = MagicMock()
    repository = AssignmentRepository(db)
    repository.acquire_overlap_lock(
        USER_ID,
        ROLE_ID,
        ScopeType.COUNTRY,
        COUNTRY_ID,
    )
    statement = db.execute.call_args.args[0]
    sql, params = _compiled(statement)
    assert "pg_advisory_xact_lock" in sql
    assert "hashtextextended" in sql
    assert str(USER_ID) not in sql
    assert params["lock_identity"] == (
        f"{USER_ID}:{ROLE_ID}:COUNTRY:{COUNTRY_ID}"
    )


def test_overlap_query_is_parameterized_and_filters_active_windows() -> None:
    """Detect intersecting live grants without literal SQL values."""
    db = MagicMock()
    db.scalar.return_value = True
    repository = AssignmentRepository(db)
    result = repository.has_active_overlap(
        user_id=USER_ID,
        role_id=ROLE_ID,
        scope_type=ScopeType.COUNTRY,
        scope_id=COUNTRY_ID,
        valid_from=NOW,
        valid_to=NOW + timedelta(days=1),
        now=NOW,
    )
    statement = db.scalar.call_args.args[0]
    sql, params = _compiled(statement)
    assert result is True
    assert "revoked_at IS NULL" in sql
    assert "valid_to IS NULL OR" in sql
    assert "valid_from IS NULL OR" in sql
    assert str(USER_ID) not in sql
    assert USER_ID in params.values()
    assert ROLE_ID in params.values()


def test_active_projection_query_filters_role_permission_and_validity() -> None:
    """Resolve only current active role and permission mappings."""
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    repository = AssignmentRepository(db)
    assert repository.list_active_projections(USER_ID, NOW) == []
    statement = db.execute.call_args.args[0]
    sql, params = _compiled(statement)
    assert "JOIN iam.role" in sql
    assert "JOIN iam.role_permission" in sql
    assert "JOIN iam.permission" in sql
    assert "user_role_assignment.revoked_at IS NULL" in sql
    assert "user_role_assignment.valid_from IS NULL OR" in sql
    assert "user_role_assignment.valid_to IS NULL OR" in sql
    assert "role.status" in sql
    assert "permission.status" in sql
    assert str(USER_ID) not in sql
    assert USER_ID in params.values()


def test_projection_normalizes_persisted_scope_string() -> None:
    """Convert string-backed scope columns into policy enum values."""
    assignment = SimpleNamespace(
        assignment_id=UUID(int=4),
        scope_type="COUNTRY",
        scope_id=COUNTRY_ID,
        country_id=COUNTRY_ID,
        vendor_id=None,
        valid_from=None,
        valid_to=None,
        revoked_at=None,
    )
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        (assignment, "iam.assignment.read"),
    ]
    projection = AssignmentRepository(db).list_active_projections(USER_ID, NOW)[
        0
    ]
    assert projection.scope.scope_type is ScopeType.COUNTRY


def test_assignment_list_applies_scope_cursor_order_and_limit_in_sql() -> None:
    """Filter country rows before loading and bound cursor pagination."""
    db = MagicMock()
    db.execute.return_value.all.return_value = []
    repository = AssignmentRepository(db)
    rows = repository.list_assignments(
        user_id=USER_ID,
        country_id=COUNTRY_ID,
        cursor=CURSOR_ID,
        limit=50,
    )
    assert rows == []
    statement = db.execute.call_args.args[0]
    sql, params = _compiled(statement)
    assert "user_role_assignment.user_id" in sql
    assert "user_role_assignment.country_id" in sql
    assert "user_role_assignment.assignment_id >" in sql
    assert "ORDER BY iam.user_role_assignment.assignment_id" in sql
    assert "LIMIT" in sql
    assert USER_ID in params.values()
    assert COUNTRY_ID in params.values()
    assert CURSOR_ID in params.values()
    assert 50 in params.values()
