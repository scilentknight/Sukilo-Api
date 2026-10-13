"""Test the Phase 4 customer migration chain."""

import importlib.util
from pathlib import Path
from types import ModuleType

VERSIONS = Path(__file__).parents[3] / "alembic" / "versions"


def _load_migration(filename: str) -> ModuleType:
    """Load one migration module from its revision filename."""
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(filename, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration {filename}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_customer_migrations_form_linear_0014_through_0016_chain() -> None:
    """Link each Phase 4 revision to its immediate predecessor."""
    expected = {
        "20261012_0014_create_iam_user_preference.py": (
            "20261012_0014",
            "20261012_0013",
        ),
        "20261012_0015_create_iam_consent_record.py": (
            "20261012_0015",
            "20261012_0014",
        ),
        "20261012_0016_create_iam_address.py": (
            "20261012_0016",
            "20261012_0015",
        ),
    }
    for filename, revisions in expected.items():
        migration = _load_migration(filename)
        assert (migration.revision, migration.down_revision) == revisions

EXPORT_MIGRATION = "20261012_0017_create_iam_data_export_request.py"
RLS_MIGRATION = "20261012_0018_enable_iam_rls.py"
PROTECTED_TABLES = (
    "user_role_assignment",
    "consent_record",
    "address",
    "data_export_request",
)


def test_phase5_migrations_chain_from_0016_through_0018() -> None:
    """Link the export table and RLS revisions directly after 0016."""
    export = _load_migration(EXPORT_MIGRATION)
    rls = _load_migration(RLS_MIGRATION)
    assert (export.revision, export.down_revision) == (
        "20261012_0017",
        "20261012_0016",
    )
    assert (rls.revision, rls.down_revision) == (
        "20261012_0018",
        "20261012_0017",
    )


def test_rls_migration_enables_and_forces_only_protected_tables() -> None:
    """Enable and force RLS on four tables and never on user_preference."""
    rls = _load_migration(RLS_MIGRATION)
    sql = list(rls.ENABLE_RLS_STATEMENTS)
    for table in PROTECTED_TABLES:
        assert f"ALTER TABLE iam.{table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE iam.{table} FORCE ROW LEVEL SECURITY" in sql
    assert len(sql) == 2 * len(PROTECTED_TABLES)
    assert tuple(rls.RLS_TABLES) == PROTECTED_TABLES
    for statement in rls.UPGRADE_STATEMENTS:
        if "ROW LEVEL SECURITY" in statement or "CREATE POLICY" in statement:
            assert "user_preference" not in statement
            assert "user_account" not in statement


def test_rls_migration_policies_deny_without_context_and_use_local_settings() -> None:
    """Require modes in every policy and avoid session-scoped settings."""
    rls = _load_migration(RLS_MIGRATION)
    joined = "\n".join(rls.UPGRADE_STATEMENTS)
    assert "set_config" not in joined
    assert "SET SESSION" not in joined.upper()
    assert "SET app." not in joined
    assert "SECURITY DEFINER" not in joined.upper()
    assert "iam.setting_uuid" in joined
    for statement in rls.POLICY_STATEMENTS:
        assert "app.authorization_mode" in statement
        assert "PERMISSIVE" in statement
        assert " TO sukilo_runtime" in statement or " TO sukilo_iam_jobs" in statement
        assert " FOR DELETE " not in statement
        assert " FOR ALL " not in statement


def test_rls_migration_policy_rules_follow_phase5_spec() -> None:
    """Spot-check mode, purpose, and consent/address/export rules."""
    rls = _load_migration(RLS_MIGRATION)
    by_name = {spec.name: spec for spec in rls.POLICIES}
    assert not [s for s in rls.POLICIES if s.table == "consent_record"
                and s.command in {"UPDATE", "DELETE"}]
    assert "'SYSTEM_JOB'" in by_name["consent_job_export_select"].using
    assert "'CUSTOMER_EXPORT'" in by_name["consent_job_export_select"].using
    assert "'RETENTION'" in by_name["address_job_retention_update"].using
    assert "'ACCOUNT_CLOSURE'" in by_name["address_closure_update"].using
    assert "country_id" not in by_name["address_closure_update"].using
    assert "deleted_at IS NULL" in by_name["address_self_update"].using
    assert "deleted_at IS NULL" in by_name["address_self_select"].using
    assert "status = 'PENDING'" in by_name["export_self_insert"].check
    assert "GLOBAL_ASSIGNMENT_READ" in by_name["assignment_global_read_select"].using
    assert not [s for s in rls.POLICIES
                if s.table == "user_role_assignment" and s.role != "sukilo_runtime"]


def test_rls_migration_roles_and_grants_are_least_privilege() -> None:
    """Create the four roles safely and keep grants narrow."""
    rls = _load_migration(RLS_MIGRATION)
    roles = "\n".join(rls.ROLE_STATEMENTS)
    for role in (
        "sukilo_migrator",
        "sukilo_runtime",
        "sukilo_iam_jobs",
        "sukilo_readonly_ops",
    ):
        assert f"rolname = '{role}'" in roles
    assert "sukilo_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in roles
    assert "NOREPLICATION BYPASSRLS" in roles
    for role in ("sukilo_runtime", "sukilo_iam_jobs", "sukilo_readonly_ops"):
        assert f"CREATE ROLE {role} NOLOGIN NOSUPERUSER" in roles
    assert roles.count("NOBYPASSRLS") == 3
    runtime = rls.RUNTIME_GRANTS
    assert "UPDATE" not in runtime["consent_record"]
    assert "DELETE" not in runtime["consent_record"]
    assert all("DELETE" not in privileges for privileges in runtime.values())
    assert runtime["role"] == ("SELECT",)
    assert "user_preference" in runtime
    assert rls.JOB_GRANTS == {
        "consent_record": ("SELECT",),
        "address": ("SELECT", "UPDATE"),
        "data_export_request": ("SELECT", "UPDATE"),
    }
    grants = "\n".join(rls.GRANT_STATEMENTS)
    assert "TO sukilo_iam_jobs" in grants
    assert "user_role_assignment TO sukilo_iam_jobs" not in grants
    assert "ALTER" not in grants.upper().replace("ALTERNATE", "")


def test_rls_migration_adds_safe_helpers_and_triggers() -> None:
    """Keep helpers invoker-safe and reject runtime SUPER_ADMIN inserts."""
    rls = _load_migration(RLS_MIGRATION)
    functions = "\n".join(rls.FUNCTION_STATEMENTS)
    assert "CREATE FUNCTION iam.setting_uuid(setting_name text)" in functions
    assert "SECURITY INVOKER" in functions
    assert "REVOKE ALL ON FUNCTION iam.setting_uuid(text) FROM PUBLIC" in functions
    triggers = "\n".join(rls.TRIGGER_STATEMENTS)
    assert "r.code = 'SUPER_ADMIN'" in triggers
    assert "BEFORE INSERT OR UPDATE OF role_id ON iam.user_role_assignment" in triggers
    assert "SET search_path = pg_catalog, iam" in triggers
    assert "sukilo_runtime" in triggers and "sukilo_iam_jobs" in triggers
