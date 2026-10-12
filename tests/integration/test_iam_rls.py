"""Exercise IAM row-level security against a migrated PostgreSQL database.

These tests are read-only and skip when the configured database is
unreachable, lacks the Phase 5 migration, or does not allow role switching.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database_context import (
    DATABASE_CONTEXT_SETTINGS,
    AuthorizationMode,
    DatabaseRequestContext,
    apply_database_context,
)

PROTECTED_TABLES = (
    "user_role_assignment",
    "consent_record",
    "address",
    "data_export_request",
)


@pytest.fixture()
def connection() -> Iterator[Connection]:
    """Yield a pooled connection, skipping when PostgreSQL is unavailable."""
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 2},
    )
    try:
        live = engine.connect()
    except OperationalError:
        engine.dispose()
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        yield live
    finally:
        live.close()
        engine.dispose()


def _current_settings(live: Connection) -> dict[str, str | None]:
    """Read every request setting visible to the current transaction."""
    return {
        name: live.execute(
            text("SELECT current_setting(:name, true)"),
            {"name": name},
        ).scalar_one()
        for name in DATABASE_CONTEXT_SETTINGS
    }


def _require_phase5_schema(live: Connection) -> None:
    """Skip unless the RLS migration and runtime role are installed."""
    try:
        forced = live.execute(
            text(
                "SELECT count(*) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'iam' AND c.relforcerowsecurity "
                "AND c.relname = ANY(:tables)"
            ),
            {"tables": list(PROTECTED_TABLES)},
        ).scalar_one()
        role = live.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = 'sukilo_runtime'")
        ).scalar_one_or_none()
    except DBAPIError:
        live.rollback()
        pytest.skip("IAM schema is not migrated in the configured database.")
    live.rollback()
    if forced != len(PROTECTED_TABLES) or role is None:
        pytest.skip("Phase 5 RLS migration is not applied.")


def test_context_does_not_leak_to_next_pooled_transaction(
    connection: Connection,
) -> None:
    """Start a reused pooled connection with no previous request context."""
    session = Session(bind=connection)
    context = DatabaseRequestContext(
        authorization_mode=AuthorizationMode.SELF,
        actor_user_id=uuid4(),
        effective_user_id=uuid4(),
        effective_country_id=uuid4(),
    )
    apply_database_context(session, context)
    assert _current_settings(connection)["app.authorization_mode"] == "SELF"
    session.rollback()
    connection.rollback()
    leaked = _current_settings(connection)
    assert all(value in (None, "") for value in leaked.values())


def test_runtime_without_context_sees_no_protected_rows(
    connection: Connection,
) -> None:
    """Return no rows from protected tables when no context is set."""
    _require_phase5_schema(connection)
    try:
        connection.execute(text("SET LOCAL ROLE sukilo_runtime"))
    except DBAPIError:
        connection.rollback()
        pytest.skip("Connected role cannot assume sukilo_runtime.")
    try:
        for table in PROTECTED_TABLES:
            count = connection.execute(
                text(f"SELECT count(*) FROM iam.{table}")
            ).scalar_one()
            assert count == 0
    finally:
        connection.rollback()
