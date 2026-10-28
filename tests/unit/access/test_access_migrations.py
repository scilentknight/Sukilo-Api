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
    """Keep the original seed stable and the vendor seed aligned with the catalog."""
    base = _load_migration("20261012_0013_seed_iam_access_catalog.py")
    added = _load_migration("20261028_0025_seed_vendor_access.py")
    store = _load_migration("20261028_0030_seed_store_permissions.py")
    taxonomy = _load_migration("20261028_0035_seed_catalog_permissions.py")
    offers = _load_migration("20261028_0041_seed_offer_permissions.py")
    added_permissions = (
        set(added.NEW_PERMISSION_CODES)
        | set(store.NEW_PERMISSION_CODES)
        | set(taxonomy.NEW_PERMISSION_CODES)
        | set(offers.NEW_PERMISSION_CODES)
    )
    base_roles = {
        code: (UUID(role_id), name, scope_type)
        for role_id, code, name, scope_type in base.ROLES
    }
    runtime_roles = {
        role.code: (role.role_id, role.name, role.scope_type.value)
        for role in ROLES
    }
    base_permissions = {
        code: (UUID(permission_id), description)
        for permission_id, code, description in base.PERMISSIONS
    }
    runtime_permissions = {
        permission.code: (permission.permission_id, permission.description)
        for permission in PERMISSIONS
    }
    assert set(base_roles) == set(runtime_roles) - set(added.NEW_ROLE_CODES)
    assert set(added.NEW_ROLE_CODES) <= set(runtime_roles)
    for code, value in base_roles.items():
        assert runtime_roles[code] == value
    assert set(base_permissions) == set(runtime_permissions) - added_permissions
    for code, value in base_permissions.items():
        assert runtime_permissions[code] == value
    assert set(base.ROLE_PERMISSIONS["COUNTRY_OPERATIONS"]) == set()
    assert ROLE_PERMISSIONS["COUNTRY_OPERATIONS"] == frozenset(
        {
            "vendor.application.review",
            "vendor.application.approve",
            "vendor.payout.verify",
            "catalog.category.enable",
            "catalog.brand.review",
            "catalog.product.moderate",
        }
    )
    assert "vendor.application.approve" not in base.ROLE_PERMISSIONS["COUNTRY_ADMIN"]
    assert "vendor.payout.verify" not in ROLE_PERMISSIONS["VENDOR_OWNER"]
    assert set(base.ROLE_PERMISSIONS["SUPER_ADMIN"]) == (
        set(ROLE_PERMISSIONS["SUPER_ADMIN"]) - added_permissions
    )
