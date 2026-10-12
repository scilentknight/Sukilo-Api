"""Normalize identity identifiers and generate stable values."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import phonenumbers
from email_validator import EmailNotValidError, validate_email


class IdentifierValidationError(ValueError):
    """Report an invalid identity identifier without exposing its value."""


def normalize_email(value: str) -> str:
    """Normalize an email address for canonical account lookup.

    Args:
        value: Candidate email address.

    Returns:
        Canonical case-folded email address.

    Raises:
        IdentifierValidationError: If the address is invalid.
    """
    try:
        validated = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise IdentifierValidationError("Invalid email address.") from exc
    return validated.normalized.casefold()


def normalize_phone_e164(
    value: str,
    default_region: str | None = None,
) -> str:
    """Normalize a valid phone number to E.164 format.

    Args:
        value: Candidate phone number.
        default_region: ISO alpha-2 region for a national number.

    Returns:
        Phone number in E.164 format.

    Raises:
        IdentifierValidationError: If the number cannot be normalized.
    """
    try:
        parsed = phonenumbers.parse(value.strip(), default_region)
    except phonenumbers.NumberParseException as exc:
        raise IdentifierValidationError("Invalid phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise IdentifierValidationError("Invalid phone number.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def new_uuid() -> UUID:
    """Return a globally unique application identifier."""
    return uuid4()


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)
