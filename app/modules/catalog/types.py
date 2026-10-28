"""Define stable catalog value types."""

from enum import StrEnum


class RecordStatus(StrEnum):
    """Represent a canonical catalog record lifecycle."""

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class RequestStatus(StrEnum):
    """Represent a vendor taxonomy request decision."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AttributeDataType(StrEnum):
    """Represent the stored shape of one attribute."""

    TEXT = "TEXT"
    NUMBER = "NUMBER"
    OPTION = "OPTION"


class ReviewDecision(StrEnum):
    """Represent a reviewer decision on one request."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ProductStatus(StrEnum):
    """Represent a shared product lifecycle."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ModerationStatus(StrEnum):
    """Represent country review of one shared product."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OfferStatus(StrEnum):
    """Represent one seller offer lifecycle."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class PublicationStatus(StrEnum):
    """Represent whether an offer is public in one country."""

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
