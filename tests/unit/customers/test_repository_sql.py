"""Test parameterized customer repository query construction."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.customers.repository import CustomerRepository


USER_ID = UUID(int=1)
COUNTRY_ID = UUID(int=2)
CONSENT_ID = UUID(int=3)
RECORDED_AT = datetime(2026, 10, 12, tzinfo=UTC)


class SessionFake:
    """Capture SQLAlchemy statements without a database."""

    def __init__(self) -> None:
        """Initialize empty statement storage."""
        self.statement = None

    def scalars(self, statement):
        """Capture a scalar collection statement."""
        self.statement = statement
        return []


def _compiled(session: SessionFake) -> tuple[str, dict[str, object]]:
    """Compile the captured statement for PostgreSQL."""
    compiled = session.statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_current_consent_sql_uses_deterministic_latest_order() -> None:
    """Order current consent by recorded time then consent identifier."""
    session = SessionFake()
    repository = CustomerRepository(session)
    repository.list_current_consents(USER_ID, COUNTRY_ID)
    sql, params = _compiled(session)
    assert "DISTINCT ON" in sql
    assert "consent_record.recorded_at DESC" in sql
    assert "consent_record.consent_id DESC" in sql
    assert USER_ID in params.values()
    assert COUNTRY_ID in params.values()
    assert str(USER_ID) not in sql
    assert str(COUNTRY_ID) not in sql


def test_history_sql_uses_bounded_parameterized_tuple_cursor() -> None:
    """Apply an exclusive time-and-ID cursor with a bound limit."""
    session = SessionFake()
    repository = CustomerRepository(session)
    repository.list_consent_history(
        USER_ID,
        COUNTRY_ID,
        cursor=(RECORDED_AT, CONSENT_ID),
        limit=25,
    )
    sql, params = _compiled(session)
    assert "(iam.consent_record.recorded_at, iam.consent_record.consent_id) <" in sql
    assert "ORDER BY iam.consent_record.recorded_at DESC" in sql
    assert "LIMIT" in sql
    assert RECORDED_AT in params.values()
    assert CONSENT_ID in params.values()
    assert 25 in params.values()


def test_history_sql_allows_one_pagination_sentinel_row() -> None:
    """Allow the service to probe one row beyond the public page maximum."""
    session = SessionFake()
    repository = CustomerRepository(session)
    repository.list_consent_history(
        USER_ID,
        COUNTRY_ID,
        cursor=None,
        limit=101,
    )
    _, params = _compiled(session)
    assert 101 in params.values()
    with pytest.raises(ValueError, match="limit"):
        repository.list_consent_history(
            USER_ID,
            COUNTRY_ID,
            cursor=None,
            limit=102,
        )
