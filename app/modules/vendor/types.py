"""Define stable vendor domain value types."""

from enum import StrEnum


class VendorType(StrEnum):
    """Represent the legal form of a seller organization."""

    COMPANY = "COMPANY"
    INDIVIDUAL = "INDIVIDUAL"
    SOLE_TRADER = "SOLE_TRADER"


class VendorStatus(StrEnum):
    """Represent the global lifecycle of a seller organization."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class OnboardingStatus(StrEnum):
    """Represent one country application decision."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class VerificationStatus(StrEnum):
    """Represent a country or document verification decision."""

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class MemberStatus(StrEnum):
    """Represent membership of one account in one organization."""

    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
