"""Test keyed digests and opaque security-token generation."""

import importlib.util
from types import ModuleType


def _load_security() -> ModuleType:
    """Load security primitives after asserting that they exist."""
    module_name = "app.core.security"
    assert importlib.util.find_spec(module_name) is not None
    return __import__(module_name, fromlist=["DigestService"])


def test_digest_is_self_describing_and_verifiable() -> None:
    """Encode key version and algorithm without exposing input data."""
    security = _load_security()
    service = security.DigestService(
        active_key_id="v2",
        peppers={"v1": b"previous-pepper", "v2": b"active-pepper"},
    )
    encoded = service.digest("refresh", "raw-token-value")
    assert encoded.startswith("v2$hmac-sha256$")
    assert "raw-token-value" not in encoded
    assert service.verify(encoded, "refresh", "raw-token-value")
    assert not service.verify(encoded, "refresh", "wrong-token-value")


def test_digest_verifies_previous_key_during_rotation() -> None:
    """Verify existing digests while writing only with the active key."""
    security = _load_security()
    old_service = security.DigestService(
        active_key_id="v1",
        peppers={"v1": b"previous-pepper"},
    )
    encoded = old_service.digest("otp", "challenge", "123456")
    current_service = security.DigestService(
        active_key_id="v2",
        peppers={"v1": b"previous-pepper", "v2": b"active-pepper"},
    )
    assert current_service.verify(encoded, "otp", "challenge", "123456")
    assert current_service.needs_rotation(encoded)


def test_digest_rejects_unknown_key_and_malformed_value() -> None:
    """Fail closed for unknown or malformed digest metadata."""
    security = _load_security()
    service = security.DigestService(
        active_key_id="v2",
        peppers={"v2": b"active-pepper"},
    )
    assert not service.verify(
        "unknown$hmac-sha256$ZmFrZQ",
        "refresh",
        "value",
    )
    assert not service.verify("malformed", "refresh", "value")


def test_opaque_token_contains_at_least_256_bits_of_entropy() -> None:
    """Generate distinct URL-safe opaque tokens with sufficient entropy."""
    security = _load_security()
    first = security.generate_opaque_token()
    second = security.generate_opaque_token()
    assert first != second
    assert len(first) >= 43
    assert all(character.isalnum() or character in "-_" for character in first)
