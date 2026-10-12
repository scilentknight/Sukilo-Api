"""Define stable identity domain value types."""

from enum import StrEnum


class AccountStatus(StrEnum):
    """Represent the lifecycle state of a user account."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class AuthProvider(StrEnum):
    """Represent a linked authentication provider."""

    LOCAL = "LOCAL"
