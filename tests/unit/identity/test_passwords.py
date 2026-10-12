"""Test Argon2id password hashing and policy behavior."""

import importlib.util
from types import ModuleType

import pytest


def _load_passwords() -> ModuleType:
    """Load password utilities after asserting that they exist."""
    module_name = "app.modules.identity.passwords"
    assert importlib.util.find_spec(module_name) is not None
    return __import__(module_name, fromlist=["PasswordHasher"])


def test_password_hasher_round_trips_valid_password() -> None:
    """Hash with Argon2id and verify the original password."""
    passwords = _load_passwords()
    hasher = passwords.PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
    )
    encoded = hasher.hash_password("correct horse battery staple")
    verification = hasher.verify_password(
        "correct horse battery staple",
        encoded,
    )
    assert encoded.startswith("$argon2id$")
    assert verification.valid
    assert not verification.needs_rehash


def test_password_hasher_rejects_wrong_password_without_raising() -> None:
    """Return an invalid result for the wrong password."""
    passwords = _load_passwords()
    hasher = passwords.PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
    )
    encoded = hasher.hash_password("correct horse battery staple")
    verification = hasher.verify_password("wrong password value", encoded)
    assert not verification.valid
    assert not verification.needs_rehash


def test_password_hasher_marks_outdated_parameters_for_rehash() -> None:
    """Request a rehash when deployment parameters change."""
    passwords = _load_passwords()
    old_hasher = passwords.PasswordHasher(
        time_cost=1,
        memory_cost=512,
        parallelism=1,
    )
    current_hasher = passwords.PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
    )
    encoded = old_hasher.hash_password("correct horse battery staple")
    verification = current_hasher.verify_password(
        "correct horse battery staple",
        encoded,
    )
    assert verification.valid
    assert verification.needs_rehash


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "x" * 129,
    ],
)
def test_password_policy_rejects_length_boundaries(password: str) -> None:
    """Reject passwords outside the accepted work bounds."""
    passwords = _load_passwords()
    hasher = passwords.PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
    )
    with pytest.raises(passwords.PasswordPolicyError) as exc_info:
        hasher.hash_password(password)
    assert password not in str(exc_info.value)


def test_password_policy_preserves_whitespace() -> None:
    """Treat password whitespace as significant content."""
    passwords = _load_passwords()
    hasher = passwords.PasswordHasher(
        time_cost=1,
        memory_cost=1024,
        parallelism=1,
    )
    encoded = hasher.hash_password("  valid password value  ")
    assert hasher.verify_password("  valid password value  ", encoded).valid
    assert not hasher.verify_password("valid password value", encoded).valid
