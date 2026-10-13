"""Define stable customer domain value types."""

from enum import StrEnum


class ConsentType(StrEnum):
    """Identify one append-only consent category."""

    TERMS = "TERMS"
    PRIVACY = "PRIVACY"
    MARKETING_EMAIL = "MARKETING_EMAIL"
    MARKETING_SMS = "MARKETING_SMS"
    ANALYTICS = "ANALYTICS"


class ConsentSource(StrEnum):
    """Identify how a consent record was captured."""

    WEB = "WEB"
    APP = "APP"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


class AddressType(StrEnum):
    """Identify a customer-owned address classification."""

    HOME = "HOME"
    OFFICE = "OFFICE"
    OTHER = "OTHER"


class DefaultKind(StrEnum):
    """Identify which default address flag to change."""

    SHIPPING = "SHIPPING"
    BILLING = "BILLING"


class DataExportStatus(StrEnum):
    """Identify the lifecycle state of a customer data export request."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    DOWNLOADED = "DOWNLOADED"
