"""Test vendor SQLAlchemy model contracts."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from app.core.database import Base
from app.modules.vendor.models import Vendor, VendorCountry, VendorMember
from app.modules.vendor.types import MemberStatus, VendorStatus, VendorType

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


def _check_names(table: object) -> set[str | None]:
    """Return check constraint names declared on one table."""
    return {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }


def test_vendor_type_and_status_reject_unknown_values() -> None:
    """Reject vendor types and statuses outside the closed sets."""
    with pytest.raises(ValueError):
        VendorType("SHOP")
    with pytest.raises(ValueError):
        VendorStatus("ARCHIVED")
    with pytest.raises(ValueError):
        MemberStatus("OWNER")


def test_vendor_tables_use_vendor_schema_and_uuid_keys() -> None:
    """Register organization, country, and member tables under vendor."""
    import app.models
    assert app.models.Vendor is Vendor
    assert app.models.VendorCountry is VendorCountry
    assert app.models.VendorMember is VendorMember
    assert Vendor.__table__.fullname == "vendor.vendor"
    assert VendorCountry.__table__.fullname == "vendor.vendor_country"
    assert VendorMember.__table__.fullname == "vendor.vendor_member"
    assert Vendor.__table__.c.vendor_id.primary_key
    assert VendorCountry.__table__.c.vendor_country_id.primary_key
    assert VendorMember.__table__.c.vendor_member_id.primary_key
    assert {
        "vendor.vendor",
        "vendor.vendor_country",
        "vendor.vendor_member",
    }.issubset(Base.metadata.tables)


def test_vendor_status_columns_are_varchar_enums() -> None:
    """Persist closed status sets as constrained strings."""
    assert Vendor.__table__.c.vendor_type.type.native_enum is False
    assert Vendor.__table__.c.status.type.python_type is VendorStatus
    assert VendorCountry.__table__.c.onboarding_status.type.native_enum is False
    assert VendorMember.__table__.c.status.type.python_type is MemberStatus
    assert "ck_vendor_vendor_type" in _check_names(Vendor.__table__)
    assert "ck_vendor_status" in _check_names(Vendor.__table__)
    assert "ck_vendor_country_onboarding_status" in _check_names(
        VendorCountry.__table__,
    )
    assert "ck_vendor_member_active_requires_accepted_at" in _check_names(
        VendorMember.__table__,
    )


def test_vendor_indexes_and_restricting_foreign_keys() -> None:
    """Keep country and member uniqueness and block parent deletes."""
    vendor_indexes = {index.name for index in Vendor.__table__.indexes}
    country_indexes = {index.name for index in VendorCountry.__table__.indexes}
    country_names = {constraint.name for constraint in VendorCountry.__table__.constraints}
    member_names = {constraint.name for constraint in VendorMember.__table__.constraints}
    assert {"ix_vendor_created_by_user", "ix_vendor_status"}.issubset(vendor_indexes)
    assert "ix_vendor_country_country_status" in country_indexes
    assert "uq_vendor_country_vendor_country" in country_names
    assert "uq_vendor_member_vendor_user" in member_names
    foreign_keys = {
        key.target_fullname: key.ondelete
        for key in (
            *Vendor.__table__.foreign_keys,
            *VendorCountry.__table__.foreign_keys,
            *VendorMember.__table__.foreign_keys,
        )
    }
    assert foreign_keys["iam.user_account.user_id"] == "RESTRICT"
    assert foreign_keys["vendor.vendor.vendor_id"] == "RESTRICT"


def test_vendor_representation_omits_registration_and_tax_numbers() -> None:
    """Keep registration and tax numbers out of diagnostic representations."""
    vendor = Vendor(
        legal_name="Acme Trading",
        display_name="Acme",
        registration_number="REG-SECRET-19",
        tax_number="TAX-SECRET-19",
    )
    country = VendorCountry(
        business_registration_no="BIZ-SECRET-19",
        tax_registration_no="CTAX-SECRET-19",
    )
    assert "REG-SECRET-19" not in repr(vendor)
    assert "TAX-SECRET-19" not in repr(vendor)
    assert "registration_number" not in repr(vendor)
    assert "tax_number" not in repr(vendor)
    assert "BIZ-SECRET-19" not in repr(country)
    assert "CTAX-SECRET-19" not in repr(country)


def test_create_table_sql_uses_vendor_schema() -> None:
    """Emit vendor.vendor rather than a public seller table."""
    sql = str(CreateTable(Vendor.__table__).compile(dialect=postgresql.dialect()))
    assert "vendor.vendor" in sql
    assert "public.vendor" not in sql


def test_vendor_migrations_follow_identity_head() -> None:
    """Link each vendor revision directly after the identity chain."""
    expected = {
        "20261028_0019_create_vendor_schema.py": (
            "20261028_0019",
            "20261012_0018",
        ),
        "20261028_0020_create_vendor_vendor.py": (
            "20261028_0020",
            "20261028_0019",
        ),
        "20261028_0021_create_vendor_country.py": (
            "20261028_0021",
            "20261028_0020",
        ),
        "20261028_0022_create_vendor_member.py": (
            "20261028_0022",
            "20261028_0021",
        ),
        "20261028_0023_create_vendor_document.py": (
            "20261028_0023",
            "20261028_0022",
        ),
        "20261028_0024_create_vendor_invitation.py": (
            "20261028_0024",
            "20261028_0023",
        ),
        "20261028_0025_seed_vendor_access.py": (
            "20261028_0025",
            "20261028_0024",
        ),
    }
    for filename, revisions in expected.items():
        migration = _load_migration(filename)
        assert (migration.revision, migration.down_revision) == revisions
