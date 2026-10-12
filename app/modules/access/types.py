"""Define stable access-control lifecycle and scope values."""

from enum import StrEnum


class ScopeType(StrEnum):
    """Identify the resource boundary attached to an assignment."""

    GLOBAL = "GLOBAL"
    COUNTRY = "COUNTRY"
    VENDOR = "VENDOR"
    STORE = "STORE"


class RoleStatus(StrEnum):
    """Identify whether a role can participate in authorization."""

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class PermissionStatus(StrEnum):
    """Identify whether a permission remains available for evaluation."""

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
