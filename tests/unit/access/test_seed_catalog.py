"""Test deterministic Phase 3 role and permission catalog definitions."""

import re

from app.modules.access.catalog import PERMISSIONS, ROLES, role_permission_codes

_PERMISSION_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$")
from app.modules.access.types import ScopeType


EXPECTED_PERMISSIONS = {
    "iam.role.read",
    "iam.permission.read",
    "iam.assignment.read",
    "iam.assignment.grant",
    "iam.assignment.revoke",
    "iam.session.read",
    "iam.session.revoke",
    "customer.profile.read",
    "customer.profile.write",
    "customer.preference.write",
    "customer.consent.write",
    "customer.address.read",
    "customer.address.write",
    "country.settings.read",
    "country.settings.write",
    "vendor.approve",
    "vendor.application.read",
    "vendor.application.submit",
    "vendor.application.review",
    "vendor.application.approve",
    "vendor.staff.invite",
    "vendor.staff.revoke",
    "vendor.store.write",
    "vendor.warehouse.write",
    "vendor.payout.verify",
    "catalog.category.write",
    "catalog.category.enable",
    "catalog.brand.review",
    "catalog.taxonomy.request",
    "catalog.product.write",
    "order.cancel",
    "refund.approve",
    "finance.payout.release",
}


def test_permission_codes_match_the_database_format() -> None:
    """Accept both domain.action and domain.resource.action codes."""
    assert all(_PERMISSION_CODE.fullmatch(entry.code) for entry in PERMISSIONS)


def test_permission_catalog_has_stable_unique_ids() -> None:
    """Define every planned permission with a deterministic unique ID."""
    assert {entry.code for entry in PERMISSIONS} == EXPECTED_PERMISSIONS
    assert len({entry.permission_id for entry in PERMISSIONS}) == len(PERMISSIONS)


def test_initial_roles_have_expected_scopes() -> None:
    """Define only the initial system roles with stable IDs."""
    assert {
        entry.code: entry.scope_type
        for entry in ROLES
    } == {
        "SUPER_ADMIN": ScopeType.GLOBAL,
        "COUNTRY_ADMIN": ScopeType.COUNTRY,
        "COUNTRY_FINANCE": ScopeType.COUNTRY,
        "COUNTRY_OPERATIONS": ScopeType.COUNTRY,
        "MODERATOR_SUPPORT": ScopeType.COUNTRY,
        "VENDOR_OWNER": ScopeType.VENDOR,
        "VENDOR_STAFF": ScopeType.VENDOR,
    }
    assert len({entry.role_id for entry in ROLES}) == len(ROLES)


def test_super_admin_receives_complete_catalog() -> None:
    """Map every current permission explicitly to the super admin role."""
    assert role_permission_codes("SUPER_ADMIN") == EXPECTED_PERMISSIONS


def test_country_admin_mappings_are_explicit_and_bounded() -> None:
    """Avoid granting future-domain capabilities through seeded country roles."""
    implemented = {
        "iam.role.read",
        "iam.permission.read",
        "iam.assignment.read",
        "iam.assignment.grant",
        "iam.assignment.revoke",
        "iam.session.read",
        "iam.session.revoke",
    }
    assert role_permission_codes("COUNTRY_ADMIN") == implemented
    assert role_permission_codes("COUNTRY_FINANCE") == set()
    assert role_permission_codes("COUNTRY_OPERATIONS") == {
        "vendor.application.review",
        "vendor.application.approve",
        "vendor.payout.verify",
        "catalog.category.enable",
        "catalog.brand.review",
    }
    assert role_permission_codes("MODERATOR_SUPPORT") == set()
    assert role_permission_codes("VENDOR_OWNER") == {
        "vendor.staff.invite",
        "vendor.staff.revoke",
        "vendor.store.write",
        "vendor.warehouse.write",
        "catalog.taxonomy.request",
    }
    assert role_permission_codes("VENDOR_STAFF") == set()
    assert "vendor.application.approve" not in role_permission_codes("COUNTRY_ADMIN")
    assert "catalog.category.write" not in role_permission_codes("COUNTRY_OPERATIONS")
    assert "catalog.category.write" not in role_permission_codes("VENDOR_OWNER")
