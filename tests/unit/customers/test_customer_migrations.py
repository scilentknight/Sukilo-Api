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
