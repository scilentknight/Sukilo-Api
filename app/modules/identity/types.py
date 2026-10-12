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


class OtpChannel(StrEnum):
    """Represent an OTP delivery channel."""

    EMAIL = "EMAIL"
    SMS = "SMS"


class OtpPurpose(StrEnum):
    """Represent an OTP challenge purpose."""

    REGISTER = "REGISTER"
    LOGIN = "LOGIN"
    RESET = "RESET"
    VERIFY_EMAIL = "VERIFY_EMAIL"
    VERIFY_PHONE = "VERIFY_PHONE"
    STEP_UP = "STEP_UP"


class GrantPurpose(StrEnum):
    """Represent a single-use authorization purpose."""

    RESET_PASSWORD = "RESET_PASSWORD"
    STEP_UP = "STEP_UP"
    CHANGE_EMAIL = "CHANGE_EMAIL"
    CHANGE_PHONE = "CHANGE_PHONE"
    CLOSE_ACCOUNT = "CLOSE_ACCOUNT"
    DATA_EXPORT = "DATA_EXPORT"
    DOWNLOAD_EXPORT = "DOWNLOAD_EXPORT"
