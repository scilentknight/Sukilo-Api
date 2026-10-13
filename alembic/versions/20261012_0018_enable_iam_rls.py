"""Enable selective IAM row-level security and least-privilege roles.

Revision ID: 20261012_0018
Revises: 20261012_0017
Create Date: 2026-10-12

The migration must run as a role that may create roles (and, on first run,
a BYPASSRLS role). It never applies session-scoped settings: policies read
transaction-local ``app.*`` settings through ``iam.setting_uuid`` and
``iam.setting_text``, which resolve missing or malformed values to NULL so
that every comparison denies access.
"""

from collections.abc import Sequence
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0018"
down_revision: str | Sequence[str] | None = "20261012_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MIGRATOR_ROLE = "sukilo_migrator"
RUNTIME_ROLE = "sukilo_runtime"
JOBS_ROLE = "sukilo_iam_jobs"
READONLY_ROLE = "sukilo_readonly_ops"
RLS_TABLES: tuple[str, ...] = (
    "user_role_assignment",
    "consent_record",
    "address",
    "data_export_request",
)
_RW = ("SELECT", "INSERT", "UPDATE")
RUNTIME_GRANTS: dict[str, tuple[str, ...]] = {
    "user_account": _RW,
    "auth_identity": _RW,
    "otp_challenge": _RW,
    "user_session": _RW,
    "auth_grant": _RW,
    "role": ("SELECT",),
    "permission": ("SELECT",),
    "role_permission": ("SELECT",),
    "user_role_assignment": _RW,
    "user_preference": _RW,
    "consent_record": ("SELECT", "INSERT"),
    "address": _RW,
    "data_export_request": _RW,
}
RUNTIME_COLUMN_GRANTS: dict[str, tuple[str, ...]] = {"role": ("updated_at",)}
JOB_GRANTS: dict[str, tuple[str, ...]] = {
    "consent_record": ("SELECT",),
    "address": ("SELECT", "UPDATE"),
    "data_export_request": ("SELECT", "UPDATE"),
}
_ELEVATED = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
_LIMITED_ROLES: tuple[str, ...] = (RUNTIME_ROLE, JOBS_ROLE, READONLY_ROLE)
_UUID_PATTERN = (
    "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_USER = "iam.setting_uuid('app.effective_user_id')"
_COUNTRY = "iam.setting_uuid('app.effective_country_id')"
_MODE = "iam.setting_text('app.authorization_mode')"
_PURPOSE = "iam.setting_text('app.job_purpose')"
_ACTION = "iam.setting_text('app.action_class')"
_SELF = f"{_MODE} = 'SELF'"
_SELF_OWNER = f"{_SELF} AND user_id = {_USER} AND country_id = {_COUNTRY}"
_CLOSURE = f"{_MODE} = 'ACCOUNT_CLOSURE' AND user_id = {_USER}"
_COUNTRY_OPERATORS = (
    f"{_MODE} IN ('COUNTRY_OPERATOR', 'GLOBAL_OPERATOR') "
    f"AND country_id = {_COUNTRY}"
)
_EXPORT_JOB = (
    f"{_MODE} = 'SYSTEM_JOB' AND {_PURPOSE} = 'CUSTOMER_EXPORT' "
    f"AND user_id = {_USER} AND country_id = {_COUNTRY}"
)
_RETENTION_JOB = (
    f"{_MODE} = 'SYSTEM_JOB' AND {_PURPOSE} = 'RETENTION' "
    f"AND country_id = {_COUNTRY}"
)


class PolicySpec(NamedTuple):
    """Describe one permissive row-level security policy."""

    table: str
    name: str
    command: str
    role: str
    using: str | None = None
    check: str | None = None


POLICIES: tuple[PolicySpec, ...] = (
    PolicySpec(
        "user_role_assignment", "assignment_self_select", "SELECT",
        RUNTIME_ROLE, using=f"{_SELF} AND user_id = {_USER}",
    ),
    PolicySpec(
        "user_role_assignment", "assignment_country_select", "SELECT",
        RUNTIME_ROLE, using=_COUNTRY_OPERATORS,
    ),
    PolicySpec(
        "user_role_assignment", "assignment_global_read_select", "SELECT",
        RUNTIME_ROLE,
        using=(
            f"{_MODE} = 'GLOBAL_OPERATOR' "
            f"AND {_ACTION} = 'GLOBAL_ASSIGNMENT_READ' "
            "AND scope_type = 'GLOBAL'"
        ),
    ),
    PolicySpec(
        "user_role_assignment", "assignment_country_insert", "INSERT",
        RUNTIME_ROLE, check=_COUNTRY_OPERATORS,
    ),
    PolicySpec(
        "user_role_assignment", "assignment_country_update", "UPDATE",
        RUNTIME_ROLE, using=_COUNTRY_OPERATORS, check=_COUNTRY_OPERATORS,
    ),
    PolicySpec(
        "consent_record", "consent_self_select", "SELECT",
        RUNTIME_ROLE, using=f"{_SELF} AND user_id = {_USER}",
    ),
    PolicySpec(
        "consent_record", "consent_country_select", "SELECT",
        RUNTIME_ROLE,
        using=f"{_MODE} = 'COUNTRY_OPERATOR' AND country_id = {_COUNTRY}",
    ),
    PolicySpec(
        "consent_record", "consent_self_insert", "INSERT",
        RUNTIME_ROLE, check=_SELF_OWNER,
    ),
    PolicySpec(
        "consent_record", "consent_job_export_select", "SELECT",
        JOBS_ROLE, using=_EXPORT_JOB,
    ),
    PolicySpec(
        "address", "address_self_select", "SELECT", RUNTIME_ROLE,
        using=(
            f"{_SELF_OWNER} AND (deleted_at IS NULL "
            f"OR {_ACTION} = 'ADDRESS_SOFT_DELETE')"
        ),
    ),
    PolicySpec(
        "address", "address_self_insert", "INSERT", RUNTIME_ROLE,
        check=f"{_SELF_OWNER} AND deleted_at IS NULL",
    ),
    PolicySpec(
        "address", "address_self_update", "UPDATE", RUNTIME_ROLE,
        using=f"{_SELF_OWNER} AND deleted_at IS NULL", check=_SELF_OWNER,
    ),
    PolicySpec(
        "address", "address_closure_select", "SELECT", RUNTIME_ROLE,
        using=_CLOSURE,
    ),
    PolicySpec(
        "address", "address_closure_update", "UPDATE", RUNTIME_ROLE,
        using=_CLOSURE, check=_CLOSURE,
    ),
    PolicySpec(
        "address", "address_job_export_select", "SELECT", JOBS_ROLE,
        using=_EXPORT_JOB,
    ),
    PolicySpec(
        "address", "address_job_retention_select", "SELECT", JOBS_ROLE,
        using=_RETENTION_JOB,
    ),
    PolicySpec(
        "address", "address_job_retention_update", "UPDATE", JOBS_ROLE,
        using=_RETENTION_JOB, check=_RETENTION_JOB,
    ),
    PolicySpec(
        "data_export_request", "export_self_select", "SELECT",
        RUNTIME_ROLE,
        using=(
            f"{_SELF} AND user_id = {_USER} AND ("
            f"country_id = {_COUNTRY} OR {_ACTION} = 'EXPORT_BY_ID')"
        ),
    ),
    PolicySpec(
        "data_export_request", "export_self_insert", "INSERT",
        RUNTIME_ROLE, check=f"{_SELF_OWNER} AND status = 'PENDING'",
    ),
    PolicySpec(
        "data_export_request", "export_self_download_update", "UPDATE",
        RUNTIME_ROLE,
        using=f"{_SELF_OWNER} AND status = 'READY'",
        check=f"{_SELF_OWNER} AND status = 'DOWNLOADED'",
    ),
    PolicySpec(
        "data_export_request", "export_job_select", "SELECT",
        JOBS_ROLE, using=_EXPORT_JOB,
    ),
    PolicySpec(
        "data_export_request", "export_job_update", "UPDATE",
        JOBS_ROLE, using=_EXPORT_JOB, check=_EXPORT_JOB,
    ),
)

_ROLE_GUARD = f"current_user IN ('{RUNTIME_ROLE}', '{JOBS_ROLE}')"


def _create_role_sql(role: str, attributes: str) -> str:
    """Build an idempotent CREATE ROLE block for one role."""
    return (
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
        f"CREATE ROLE {role} NOLOGIN {attributes}; "
        "END IF; END $$"
    )


ROLE_STATEMENTS: tuple[str, ...] = (
    _create_role_sql(MIGRATOR_ROLE, f"{_ELEVATED} BYPASSRLS"),
    _create_role_sql(RUNTIME_ROLE, f"{_ELEVATED} NOBYPASSRLS"),
    _create_role_sql(JOBS_ROLE, f"{_ELEVATED} NOBYPASSRLS"),
    _create_role_sql(READONLY_ROLE, f"{_ELEVATED} NOBYPASSRLS"),
    (
        "DO $$ DECLARE offenders text; BEGIN "
        "SELECT string_agg(rolname, ', ') INTO offenders FROM pg_roles "
        "WHERE rolname IN ("
        + ", ".join(f"'{role}'" for role in _LIMITED_ROLES)
        + ") AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls); "
        "IF offenders IS NOT NULL THEN "
        "RAISE EXCEPTION USING MESSAGE = "
        "'IAM roles hold forbidden attributes: ' || offenders; "
        "END IF; END $$"
    ),
)

FUNCTION_STATEMENTS: tuple[str, ...] = (
    (
        "CREATE FUNCTION iam.setting_text(setting_name text) RETURNS text "
        "LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER "
        "AS $$ SELECT nullif(btrim(current_setting(setting_name, true)), '') $$"
    ),
    (
        "CREATE FUNCTION iam.setting_uuid(setting_name text) RETURNS uuid "
        "LANGUAGE sql STABLE PARALLEL SAFE SECURITY INVOKER "
        "AS $$ SELECT CASE WHEN iam.setting_text(setting_name) "
        f"~ '{_UUID_PATTERN}' "
        "THEN CAST(iam.setting_text(setting_name) AS uuid) END $$"
    ),
    "REVOKE ALL ON FUNCTION iam.setting_text(text) FROM PUBLIC",
    "REVOKE ALL ON FUNCTION iam.setting_uuid(text) FROM PUBLIC",
    (
        "GRANT EXECUTE ON FUNCTION iam.setting_text(text), "
        f"iam.setting_uuid(text) TO {RUNTIME_ROLE}, {JOBS_ROLE}"
    ),
)

TRIGGER_STATEMENTS: tuple[str, ...] = (
    (
        "CREATE FUNCTION iam.reject_runtime_super_admin_assignment() "
        "RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER "
        "SET search_path = pg_catalog, iam AS $$ BEGIN "
        f"IF {_ROLE_GUARD} AND EXISTS ("
        "SELECT 1 FROM iam.role AS r "
        "WHERE r.role_id = NEW.role_id AND r.code = 'SUPER_ADMIN') THEN "
        "RAISE EXCEPTION USING ERRCODE = '42501', "
        "MESSAGE = 'SUPER_ADMIN assignment is not permitted for this role'; "
        "END IF; RETURN NEW; END $$"
    ),
    (
        "CREATE TRIGGER trg_assignment_reject_runtime_super_admin "
        "BEFORE INSERT OR UPDATE OF role_id ON iam.user_role_assignment "
        "FOR EACH ROW EXECUTE FUNCTION "
        "iam.reject_runtime_super_admin_assignment()"
    ),
    (
        "CREATE FUNCTION iam.reject_address_owner_change() "
        "RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER "
        "SET search_path = pg_catalog, iam AS $$ BEGIN "
        f"IF {_ROLE_GUARD} AND (NEW.user_id IS DISTINCT FROM OLD.user_id "
        "OR NEW.country_id IS DISTINCT FROM OLD.country_id) THEN "
        "RAISE EXCEPTION USING ERRCODE = '42501', "
        "MESSAGE = 'Address owner and country are immutable'; "
        "END IF; RETURN NEW; END $$"
    ),
    (
        "CREATE TRIGGER trg_address_reject_owner_change "
        "BEFORE UPDATE OF user_id, country_id ON iam.address "
        "FOR EACH ROW EXECUTE FUNCTION iam.reject_address_owner_change()"
    ),
    "REVOKE ALL ON FUNCTION iam.reject_runtime_super_admin_assignment() "
    "FROM PUBLIC",
    "REVOKE ALL ON FUNCTION iam.reject_address_owner_change() FROM PUBLIC",
)


def _grant_sql(grants: dict[str, tuple[str, ...]], role: str) -> list[str]:
    """Build table GRANT statements for one role."""
    return [
        f"GRANT {', '.join(privileges)} ON iam.{table} TO {role}"
        for table, privileges in grants.items()
    ]


def _column_grant_sql(role: str) -> list[str]:
    """Build column-level UPDATE grants used only for row locking."""
    return [
        f"GRANT UPDATE ({', '.join(columns)}) ON iam.{table} TO {role}"
        for table, columns in RUNTIME_COLUMN_GRANTS.items()
    ]


def _grant_statements() -> tuple[str, ...]:
    """Return the complete least-privilege grant manifest as SQL."""
    limited = f"{RUNTIME_ROLE}, {JOBS_ROLE}"
    statements = [
        f"GRANT USAGE ON SCHEMA iam TO {limited}",
        f"GRANT USAGE, CREATE ON SCHEMA iam TO {MIGRATOR_ROLE}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA iam FROM {limited}, {READONLY_ROLE}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA iam FROM {limited}, {READONLY_ROLE}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA iam TO {RUNTIME_ROLE}",
    ]
    statements += _grant_sql(RUNTIME_GRANTS, RUNTIME_ROLE)
    statements += _column_grant_sql(RUNTIME_ROLE)
    statements += _grant_sql(JOB_GRANTS, JOBS_ROLE)
    return tuple(statements)


def _policy_sql(spec: PolicySpec) -> str:
    """Build one CREATE POLICY statement."""
    parts = [
        f"CREATE POLICY {spec.name} ON iam.{spec.table} "
        f"AS PERMISSIVE FOR {spec.command} TO {spec.role}"
    ]
    if spec.using is not None:
        parts.append(f"USING ({spec.using})")
    if spec.check is not None:
        parts.append(f"WITH CHECK ({spec.check})")
    return " ".join(parts)


def _enable_rls_statements() -> tuple[str, ...]:
    """Return ENABLE and FORCE ROW LEVEL SECURITY for each protected table."""
    statements: list[str] = []
    for table in RLS_TABLES:
        statements.append(f"ALTER TABLE iam.{table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE iam.{table} FORCE ROW LEVEL SECURITY")
    return tuple(statements)


def _execute_all(statements: Sequence[str]) -> None:
    """Execute each SQL statement in order."""
    for statement in statements:
        op.execute(sa.text(statement))


GRANT_STATEMENTS: tuple[str, ...] = _grant_statements()
ENABLE_RLS_STATEMENTS: tuple[str, ...] = _enable_rls_statements()
POLICY_STATEMENTS: tuple[str, ...] = tuple(_policy_sql(spec) for spec in POLICIES)
UPGRADE_STATEMENTS: tuple[str, ...] = (
    ROLE_STATEMENTS
    + FUNCTION_STATEMENTS
    + GRANT_STATEMENTS
    + ENABLE_RLS_STATEMENTS
    + POLICY_STATEMENTS
    + TRIGGER_STATEMENTS
)


def _downgrade_statements() -> tuple[str, ...]:
    """Return SQL that reverses policies, triggers, functions, and grants."""
    limited = f"{RUNTIME_ROLE}, {JOBS_ROLE}"
    statements = [
        "DROP TRIGGER IF EXISTS trg_address_reject_owner_change ON iam.address",
        "DROP TRIGGER IF EXISTS trg_assignment_reject_runtime_super_admin "
        "ON iam.user_role_assignment",
        "DROP FUNCTION IF EXISTS iam.reject_address_owner_change()",
        "DROP FUNCTION IF EXISTS iam.reject_runtime_super_admin_assignment()",
    ]
    statements += [
        f"DROP POLICY IF EXISTS {spec.name} ON iam.{spec.table}"
        for spec in POLICIES
    ]
    for table in RLS_TABLES:
        statements.append(f"ALTER TABLE iam.{table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE iam.{table} DISABLE ROW LEVEL SECURITY")
    statements += [
        "DROP FUNCTION IF EXISTS iam.setting_uuid(text)",
        "DROP FUNCTION IF EXISTS iam.setting_text(text)",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA iam FROM {limited}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA iam FROM {limited}",
        f"REVOKE ALL ON SCHEMA iam FROM {limited}, {MIGRATOR_ROLE}",
    ]
    return tuple(statements)


def upgrade() -> None:
    """Create roles, grants, helper functions, triggers, and RLS policies."""
    _execute_all(UPGRADE_STATEMENTS)


def downgrade() -> None:
    """Remove RLS policies, helpers, triggers, and role grants.

    Cluster-level roles are intentionally left in place because other
    databases and credentials may reference them.
    """
    _execute_all(_downgrade_statements())
