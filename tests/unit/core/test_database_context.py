"""Test transaction-local database request context helpers."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

from app.core import database_context
from app.core.database_context import (
    DATABASE_CONTEXT_SETTINGS,
    AuthorizationMode,
    DatabaseContextError,
    DatabaseRequestContext,
    apply_database_context,
    clear_database_context,
    validate_database_context,
)

USER_ID = uuid4()
COUNTRY_ID = uuid4()
EXPECTED_SETTINGS = (
    "app.actor_user_id",
    "app.effective_user_id",
    "app.effective_country_id",
    "app.authorization_mode",
    "app.action_class",
    "app.job_purpose",
    "app.correlation_id",
)


class RecordingSession:
    """Capture statements executed against a session-like object."""

    def __init__(self) -> None:
        """Start with no recorded statements."""
        self.calls: list[tuple[TextClause, dict[str, Any]]] = []
        self.began = False

    def execute(self, statement: TextClause, params: dict[str, Any]) -> None:
        """Record one executed statement and its bound parameters."""
        self.calls.append((statement, params))

    def begin(self) -> None:
        """Fail if the helper opens an extra transaction."""
        self.began = True


def _context(**overrides: Any) -> DatabaseRequestContext:
    """Build a valid SELF context with optional overrides."""
    values: dict[str, Any] = {
        "authorization_mode": AuthorizationMode.SELF,
        "actor_user_id": USER_ID,
        "effective_user_id": USER_ID,
    }
    values.update(overrides)
    return DatabaseRequestContext(**values)


def test_settings_match_phase5_contract() -> None:
    """Expose exactly the seven app.* settings from the Phase 5 spec."""
    assert DATABASE_CONTEXT_SETTINGS == EXPECTED_SETTINGS
    assert [mode.value for mode in AuthorizationMode] == [
        "SELF",
        "COUNTRY_OPERATOR",
        "GLOBAL_OPERATOR",
        "SYSTEM_JOB",
        "ACCOUNT_CLOSURE",
    ]


def test_context_is_frozen() -> None:
    """Prevent mutation of a trusted context after construction."""
    context = _context()
    with pytest.raises(AttributeError):
        context.effective_user_id = uuid4()  # type: ignore[misc]


@pytest.mark.parametrize(
    "mode",
    [AuthorizationMode.SELF, AuthorizationMode.ACCOUNT_CLOSURE],
)
def test_user_modes_require_effective_user(mode: AuthorizationMode) -> None:
    """Require an effective user for self-service and closure modes."""
    with pytest.raises(DatabaseContextError):
        validate_database_context(DatabaseRequestContext(authorization_mode=mode))
    validate_database_context(_context(authorization_mode=mode))


def test_country_operator_requires_country() -> None:
    """Require a country for country operators."""
    context = DatabaseRequestContext(
        authorization_mode=AuthorizationMode.COUNTRY_OPERATOR,
    )
    with pytest.raises(DatabaseContextError):
        validate_database_context(context)
    validate_database_context(
        _context(
            authorization_mode=AuthorizationMode.COUNTRY_OPERATOR,
            effective_user_id=None,
            effective_country_id=COUNTRY_ID,
        )
    )


def test_global_operator_needs_country_unless_global_assignment_read() -> None:
    """Allow a missing country only for the controlled global read action."""
    mode = AuthorizationMode.GLOBAL_OPERATOR
    with pytest.raises(DatabaseContextError):
        validate_database_context(_context(authorization_mode=mode))
    with pytest.raises(DatabaseContextError):
        validate_database_context(
            _context(authorization_mode=mode, action_class="GLOBAL_ASSIGNMENT")
        )
    validate_database_context(
        _context(authorization_mode=mode, action_class="GLOBAL_ASSIGNMENT_READ")
    )
    validate_database_context(
        _context(authorization_mode=mode, effective_country_id=COUNTRY_ID)
    )


def test_system_job_requires_purpose() -> None:
    """Reject jobs without a declared purpose."""
    mode = AuthorizationMode.SYSTEM_JOB
    with pytest.raises(DatabaseContextError):
        validate_database_context(DatabaseRequestContext(authorization_mode=mode))
    with pytest.raises(DatabaseContextError):
        validate_database_context(
            DatabaseRequestContext(authorization_mode=mode, job_purpose="")
        )


@pytest.mark.parametrize("purpose", ["CUSTOMER_EXPORT", "RETENTION"])
def test_export_and_retention_jobs_require_user_and_country(purpose: str) -> None:
    """Require both identifiers for export and retention jobs."""
    mode = AuthorizationMode.SYSTEM_JOB
    for missing in (
        {"effective_user_id": None, "effective_country_id": COUNTRY_ID},
        {"effective_user_id": USER_ID, "effective_country_id": None},
    ):
        with pytest.raises(DatabaseContextError):
            validate_database_context(
                DatabaseRequestContext(
                    authorization_mode=mode,
                    job_purpose=purpose,
                    **missing,
                )
            )
    validate_database_context(
        DatabaseRequestContext(
            authorization_mode=mode,
            job_purpose=purpose,
            effective_user_id=USER_ID,
            effective_country_id=COUNTRY_ID,
        )
    )


def test_request_body_mappings_are_rejected() -> None:
    """Refuse dictionaries and other untrusted shapes."""
    body = {"authorization_mode": "SELF", "effective_user_id": str(USER_ID)}
    session = RecordingSession()
    with pytest.raises(DatabaseContextError):
        validate_database_context(body)  # type: ignore[arg-type]
    with pytest.raises(DatabaseContextError):
        apply_database_context(session, body)  # type: ignore[arg-type]
    assert session.calls == []


def test_string_mode_and_string_ids_are_rejected() -> None:
    """Refuse untyped values that could originate from request data."""
    with pytest.raises(DatabaseContextError):
        validate_database_context(
            _context(authorization_mode="SELF")  # type: ignore[arg-type]
        )
    with pytest.raises(DatabaseContextError):
        validate_database_context(
            _context(effective_user_id=str(USER_ID))
        )
    with pytest.raises(DatabaseContextError):
        validate_database_context(_context(action_class="drop table; --"))


def test_apply_sets_every_setting_with_local_scope() -> None:
    """Issue one parameterized set_config(..., true) call per setting."""
    session = RecordingSession()
    correlation_id = uuid4()
    context = _context(
        effective_country_id=COUNTRY_ID,
        action_class="ADDRESS_SOFT_DELETE",
        correlation_id=correlation_id,
    )
    apply_database_context(session, context)
    assert not session.began
    assert [params["name"] for _, params in session.calls] == list(
        EXPECTED_SETTINGS
    )
    values = {params["name"]: params["value"] for _, params in session.calls}
    assert values["app.effective_user_id"] == str(USER_ID)
    assert values["app.effective_country_id"] == str(COUNTRY_ID)
    assert values["app.authorization_mode"] == "SELF"
    assert values["app.action_class"] == "ADDRESS_SOFT_DELETE"
    assert values["app.correlation_id"] == str(correlation_id)
    for statement, _ in session.calls:
        compiled = str(statement.compile(dialect=postgresql.dialect()))
        assert compiled == "SELECT set_config(%(name)s, %(value)s, true)"


def test_apply_stores_empty_optional_values_as_empty_strings() -> None:
    """Normalize unset optional values to empty strings."""
    session = RecordingSession()
    apply_database_context(session, _context(actor_user_id=None))
    values = {params["name"]: params["value"] for _, params in session.calls}
    assert values["app.actor_user_id"] == ""
    assert values["app.effective_country_id"] == ""
    assert values["app.action_class"] == ""
    assert values["app.job_purpose"] == ""
    assert values["app.correlation_id"] == ""


def test_apply_validates_before_executing_sql() -> None:
    """Skip all SQL when the context is invalid."""
    session = RecordingSession()
    context = DatabaseRequestContext(authorization_mode=AuthorizationMode.SELF)
    with pytest.raises(DatabaseContextError):
        apply_database_context(session, context)
    assert session.calls == []


def test_clear_resets_every_setting_to_empty_local_value() -> None:
    """Reset each setting with a transaction-local empty value."""
    session = RecordingSession()
    clear_database_context(session)
    assert [(params["name"], params["value"]) for _, params in session.calls] == [
        (name, "") for name in EXPECTED_SETTINGS
    ]
    assert all("true)" in str(statement) for statement, _ in session.calls)


def test_module_never_uses_session_scoped_settings() -> None:
    """Keep the implementation free of SET and non-local set_config."""
    source = Path(database_context.__file__).read_text(encoding="utf-8")
    assert "set_config(:name, :value, true)" in source
    assert "set_config(:name, :value, false)" not in source
    assert "SET SESSION" not in source.upper()
    assert "SET LOCAL" not in source.upper()
