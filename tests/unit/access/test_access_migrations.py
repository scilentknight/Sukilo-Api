"""Test the Phase 3 migration chain and seeded catalog."""

import importlib.util
from pathlib import Path
from types import ModuleType
from uuid import UUID

from app.modules.access.catalog import PERMISSIONS, ROLES, ROLE_PERMISSIONS


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


def test_access_migrations_form_linear_0009_through_0013_chain() -> None:
    """Link each Phase 3 revision to its immediate predecessor."""
    expected = {
        "20261012_0009_create_iam_role.py": ("20261012_0009", "20261012_0008"),
        "20261012_0010_create_iam_permission.py": (
            "20261012_0010",
            "20261012_0009",
        ),
        "20261012_0011_create_iam_role_permission.py": (
            "20261012_0011",
            "20261012_0010",
        ),
        "20261012_0012_create_iam_user_role_assignment.py": (
            "20261012_0012",
            "20261012_0011",
        ),
        "20261012_0013_seed_iam_access_catalog.py": (
            "20261012_0013",
            "20261012_0012",
        ),
    }
    for filename, revisions in expected.items():
        migration = _load_migration(filename)
        assert (migration.revision, migration.down_revision) == revisions


def test_seed_migration_matches_runtime_catalog_exactly() -> None:
    """Keep deterministic seed IDs, metadata, and mappings synchronized."""
    migration = _load_migration("20261012_0013_seed_iam_access_catalog.py")
    migration_roles = {
        code: (UUID(role_id), name, scope_type)
        for role_id, code, name, scope_type in migration.ROLES
    }
    runtime_roles = {
        role.code: (role.role_id, role.name, role.scope_type.value)
        for role in ROLES
    }
    migration_permissions = {
        code: (UUID(permission_id), description)
        for permission_id, code, description in migration.PERMISSIONS
    }
    runtime_permissions = {
        permission.code: (permission.permission_id, permission.description)
        for permission in PERMISSIONS
    }
    assert migration_roles == runtime_roles
    assert migration_permissions == runtime_permissions
    assert migration.ROLE_PERMISSIONS == ROLE_PERMISSIONS
