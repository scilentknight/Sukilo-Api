"""Apply transaction-local PostgreSQL request context for row-level security."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

GLOBAL_ASSIGNMENT_READ_ACTION = "GLOBAL_ASSIGNMENT_READ"
ADDRESS_SOFT_DELETE_ACTION = "ADDRESS_SOFT_DELETE"
EXPORT_BY_ID_ACTION = "EXPORT_BY_ID"
CUSTOMER_ADDRESS_ACTION = "CUSTOMER_ADDRESS"
CUSTOMER_CONSENT_ACTION = "CUSTOMER_CONSENT"
JOB_PURPOSE_CUSTOMER_EXPORT = "CUSTOMER_EXPORT"
JOB_PURPOSE_RETENTION = "RETENTION"

SETTING_ACTOR_USER_ID = "app.actor_user_id"
SETTING_EFFECTIVE_USER_ID = "app.effective_user_id"
SETTING_EFFECTIVE_COUNTRY_ID = "app.effective_country_id"
SETTING_AUTHORIZATION_MODE = "app.authorization_mode"
SETTING_ACTION_CLASS = "app.action_class"
SETTING_JOB_PURPOSE = "app.job_purpose"
SETTING_CORRELATION_ID = "app.correlation_id"
DATABASE_CONTEXT_SETTINGS: tuple[str, ...] = (
    SETTING_ACTOR_USER_ID,
    SETTING_EFFECTIVE_USER_ID,
    SETTING_EFFECTIVE_COUNTRY_ID,
    SETTING_AUTHORIZATION_MODE,
    SETTING_ACTION_CLASS,
    SETTING_JOB_PURPOSE,
    SETTING_CORRELATION_ID,
)

_SET_CONFIG_LOCAL = text("SELECT set_config(:name, :value, true)")
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_USER_AND_COUNTRY_JOB_PURPOSES = frozenset(
    {JOB_PURPOSE_CUSTOMER_EXPORT, JOB_PURPOSE_RETENTION}
)


class DatabaseContextError(ValueError):
    """Signal that a database request context is missing or malformed."""


class AuthorizationMode(StrEnum):
    """Identify the already-authorized database access mode."""

    SELF = "SELF"
    COUNTRY_OPERATOR = "COUNTRY_OPERATOR"
    GLOBAL_OPERATOR = "GLOBAL_OPERATOR"
    SYSTEM_JOB = "SYSTEM_JOB"
    ACCOUNT_CLOSURE = "ACCOUNT_CLOSURE"


@dataclass(frozen=True, kw_only=True)
class DatabaseRequestContext:
    """Carry trusted, server-derived values for one database transaction.

    Instances are built only from an already-authorized command context.
    Request bodies and other untrusted mappings must never be converted into
    this type without prior authorization.
    """

    authorization_mode: AuthorizationMode
    actor_user_id: UUID | None = None
    effective_user_id: UUID | None = None
    effective_country_id: UUID | None = None
    action_class: str | None = None
    job_purpose: str | None = None
    correlation_id: UUID | None = None


def validate_database_context(context: DatabaseRequestContext) -> None:
    """Reject a context that lacks the values its mode requires.

    Args:
        context: Trusted request context to check.

    Raises:
        DatabaseContextError: If the value is not a context object, has
            malformed fields, or lacks values required by its mode.
    """
    if not isinstance(context, DatabaseRequestContext):
        raise DatabaseContextError("A DatabaseRequestContext is required.")
    _validate_field_types(context)
    mode = context.authorization_mode
    if mode in (AuthorizationMode.SELF, AuthorizationMode.ACCOUNT_CLOSURE):
        _require(context.effective_user_id, "effective_user_id", mode)
    elif mode is AuthorizationMode.COUNTRY_OPERATOR:
        _require(context.effective_country_id, "effective_country_id", mode)
    elif mode is AuthorizationMode.GLOBAL_OPERATOR:
        _validate_global_operator(context)
    else:
        _validate_system_job(context)


def apply_database_context(
    session: Session,
    context: DatabaseRequestContext,
) -> None:
    """Set transaction-local database settings for the current transaction.

    The call relies on the session's existing transaction (or its autobegin)
    and never opens an extra one. Each value is written with
    ``set_config(name, value, true)`` so it ends with the transaction.

    Args:
        session: Session whose current transaction receives the context.
        context: Validated, already-authorized request context.

    Raises:
        DatabaseContextError: If the context is invalid.
    """
    validate_database_context(context)
    for name, value in _context_values(context):
        session.execute(_SET_CONFIG_LOCAL, {"name": name, "value": value})


def bind_database_context(session: object, context: DatabaseRequestContext) -> None:
    """Apply context when the session can execute SQL.

    Unit fakes that are not database sessions are left unchanged. A real
    SQLAlchemy session always receives transaction-local settings.

    Args:
        session: Candidate database session.
        context: Already-authorized request context.

    Raises:
        DatabaseContextError: If a real session receives an invalid context.
    """
    if not callable(getattr(session, "execute", None)):
        return
    apply_database_context(cast(Session, session), context)


def clear_database_context(session: Session) -> None:
    """Reset every request setting for the current transaction.

    Args:
        session: Session whose current transaction should lose its context.
    """
    for name in DATABASE_CONTEXT_SETTINGS:
        session.execute(_SET_CONFIG_LOCAL, {"name": name, "value": ""})


def _context_values(context: DatabaseRequestContext) -> tuple[tuple[str, str], ...]:
    """Return setting names paired with empty-string-normalized values."""
    return (
        (SETTING_ACTOR_USER_ID, _text_value(context.actor_user_id)),
        (SETTING_EFFECTIVE_USER_ID, _text_value(context.effective_user_id)),
        (
            SETTING_EFFECTIVE_COUNTRY_ID,
            _text_value(context.effective_country_id),
        ),
        (SETTING_AUTHORIZATION_MODE, context.authorization_mode.value),
        (SETTING_ACTION_CLASS, _text_value(context.action_class)),
        (SETTING_JOB_PURPOSE, _text_value(context.job_purpose)),
        (SETTING_CORRELATION_ID, _text_value(context.correlation_id)),
    )


def _text_value(value: UUID | str | None) -> str:
    """Convert an optional value to its stored string form."""
    return "" if value is None else str(value)


def _validate_field_types(context: DatabaseRequestContext) -> None:
    """Require enum, UUID, and code fields to have their strict types."""
    if not isinstance(context.authorization_mode, AuthorizationMode):
        raise DatabaseContextError("authorization_mode must be an enum member.")
    for name in (
        "actor_user_id",
        "effective_user_id",
        "effective_country_id",
        "correlation_id",
    ):
        value = getattr(context, name)
        if value is not None and not isinstance(value, UUID):
            raise DatabaseContextError(f"{name} must be a UUID or None.")
    for name in ("action_class", "job_purpose"):
        value = getattr(context, name)
        if value not in (None, "") and not _is_code(value):
            raise DatabaseContextError(f"{name} must be an upper-case code.")


def _is_code(value: object) -> bool:
    """Return whether a value is an upper-snake-case code string."""
    return isinstance(value, str) and _CODE_PATTERN.fullmatch(value) is not None


def _require(value: object, name: str, mode: AuthorizationMode) -> None:
    """Raise when a value required by the mode is absent."""
    if value is None or value == "":
        raise DatabaseContextError(f"{mode.value} requires {name}.")


def _validate_global_operator(context: DatabaseRequestContext) -> None:
    """Require an explicit country except for controlled global reads."""
    if context.action_class == GLOBAL_ASSIGNMENT_READ_ACTION:
        return
    _require(
        context.effective_country_id,
        "effective_country_id",
        context.authorization_mode,
    )


def _validate_system_job(context: DatabaseRequestContext) -> None:
    """Require a job purpose and, for export/retention, user and country."""
    mode = context.authorization_mode
    _require(context.job_purpose, "job_purpose", mode)
    if context.job_purpose in _USER_AND_COUNTRY_JOB_PURPOSES:
        _require(context.effective_user_id, "effective_user_id", mode)
        _require(context.effective_country_id, "effective_country_id", mode)
