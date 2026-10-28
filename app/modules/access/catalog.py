"""Define deterministic system roles and permission catalog entries."""

from dataclasses import dataclass
from uuid import UUID, uuid5

from app.modules.access.types import ScopeType

CATALOG_NAMESPACE = UUID("4927ba3e-f9b6-4c0a-a8cc-c8d01307392b")


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    """Describe one immutable permission definition."""

    permission_id: UUID
    code: str
    description: str


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """Describe one immutable system role definition."""

    role_id: UUID
    code: str
    name: str
    scope_type: ScopeType


def _permission(code: str, description: str) -> PermissionDefinition:
    return PermissionDefinition(
        permission_id=uuid5(CATALOG_NAMESPACE, f"permission:{code}"),
        code=code,
        description=description,
    )


def _role(code: str, name: str, scope_type: ScopeType) -> RoleDefinition:
    return RoleDefinition(
        role_id=uuid5(CATALOG_NAMESPACE, f"role:{code}"),
        code=code,
        name=name,
        scope_type=scope_type,
    )


PERMISSIONS = (
    _permission("iam.role.read", "Read role definitions."),
    _permission("iam.permission.read", "Read permission definitions."),
    _permission("iam.assignment.read", "Read scoped role assignments."),
    _permission("iam.assignment.grant", "Grant scoped role assignments."),
    _permission("iam.assignment.revoke", "Revoke scoped role assignments."),
    _permission("iam.session.read", "Read account session metadata."),
    _permission("iam.session.revoke", "Revoke account sessions."),
    _permission("customer.profile.read", "Read customer profiles."),
    _permission("customer.profile.write", "Update customer profiles."),
    _permission("customer.preference.write", "Update customer preferences."),
    _permission("customer.consent.write", "Record customer consent."),
    _permission("customer.address.read", "Read customer addresses."),
    _permission("customer.address.write", "Manage customer addresses."),
    _permission("country.settings.read", "Read country settings."),
    _permission("country.settings.write", "Update country settings."),
    _permission("vendor.approve", "Approve vendors."),
    _permission("vendor.application.read", "Read a seller country application."),
    _permission("vendor.application.submit", "Submit a seller country application."),
    _permission("vendor.application.review", "Review seller country applications."),
    _permission(
        "vendor.application.approve",
        "Approve or reject a seller country application.",
    ),
    _permission("vendor.staff.invite", "Invite vendor staff."),
    _permission("vendor.staff.revoke", "Revoke vendor staff."),
    _permission("vendor.store.write", "Update the vendor storefront."),
    _permission("vendor.warehouse.write", "Manage vendor warehouses."),
    _permission("vendor.payout.verify", "Verify a vendor payout account."),
    _permission("catalog.category.write", "Create canonical categories."),
    _permission("catalog.category.enable", "Enable a category in one country."),
    _permission("catalog.brand.review", "Review brand requests."),
    _permission("catalog.taxonomy.request", "Request a catalog taxonomy change."),
    _permission("catalog.product.write", "Manage catalog products."),
    _permission("order.cancel", "Cancel orders."),
    _permission("refund.approve", "Approve refunds."),
    _permission("finance.payout.release", "Release finance payouts."),
)

ROLES = (
    _role("SUPER_ADMIN", "Super Administrator", ScopeType.GLOBAL),
    _role("COUNTRY_ADMIN", "Country Administrator", ScopeType.COUNTRY),
    _role("COUNTRY_FINANCE", "Country Finance", ScopeType.COUNTRY),
    _role("COUNTRY_OPERATIONS", "Country Operations", ScopeType.COUNTRY),
    _role("MODERATOR_SUPPORT", "Moderator Support", ScopeType.COUNTRY),
    _role("VENDOR_OWNER", "Vendor Owner", ScopeType.VENDOR),
    _role("VENDOR_STAFF", "Vendor Staff", ScopeType.VENDOR),
)

_IAM_ADMIN_PERMISSIONS = frozenset(
    {
        "iam.role.read",
        "iam.permission.read",
        "iam.assignment.read",
        "iam.assignment.grant",
        "iam.assignment.revoke",
        "iam.session.read",
        "iam.session.revoke",
    }
)

ROLE_PERMISSIONS = {
    "SUPER_ADMIN": frozenset(entry.code for entry in PERMISSIONS),
    "COUNTRY_ADMIN": _IAM_ADMIN_PERMISSIONS,
    "COUNTRY_FINANCE": frozenset(),
    "COUNTRY_OPERATIONS": frozenset(
        {
            "vendor.application.review",
            "vendor.application.approve",
            "vendor.payout.verify",
            "catalog.category.enable",
            "catalog.brand.review",
        }
    ),
    "MODERATOR_SUPPORT": frozenset(),
    "VENDOR_OWNER": frozenset(
        {
            "vendor.staff.invite",
            "vendor.staff.revoke",
            "vendor.store.write",
            "vendor.warehouse.write",
            "catalog.taxonomy.request",
        }
    ),
    "VENDOR_STAFF": frozenset(),
}


def role_permission_codes(role_code: str) -> set[str]:
    """Return the explicitly seeded permissions for one role.

    Args:
        role_code: Stable role code.

    Returns:
        Permission codes assigned to the role.
    """
    return set(ROLE_PERMISSIONS.get(role_code, frozenset()))
