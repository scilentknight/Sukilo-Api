"""Test identity identifier normalization behavior."""

import importlib.util
from types import ModuleType

import pytest


def _load_identifiers_module() -> ModuleType:
    """Load the identifier module after asserting that it exists."""
    module_name = "app.core.identifiers"
    assert importlib.util.find_spec(module_name) is not None
    return __import__(module_name, fromlist=["IdentifierValidationError"])


def test_normalize_email_returns_canonical_address() -> None:
    """Normalize whitespace and email casing deterministically."""
    identifiers = _load_identifiers_module()
    assert (
        identifiers.normalize_email(" Buyer@Example.COM ")
        == "buyer@example.com"
    )


def test_normalize_email_rejects_invalid_address() -> None:
    """Reject an address that cannot be used for authentication."""
    identifiers = _load_identifiers_module()
    with pytest.raises(identifiers.IdentifierValidationError):
        identifiers.normalize_email("not-an-email")


def test_normalize_phone_returns_e164_number() -> None:
    """Normalize a Nepal mobile number to E.164 format."""
    identifiers = _load_identifiers_module()
    assert (
        identifiers.normalize_phone_e164("9841234567", "NP")
        == "+9779841234567"
    )


def test_normalize_phone_rejects_invalid_number() -> None:
    """Reject a phone number that is not valid for its region."""
    identifiers = _load_identifiers_module()
    with pytest.raises(identifiers.IdentifierValidationError):
        identifiers.normalize_phone_e164("123", "NP")
