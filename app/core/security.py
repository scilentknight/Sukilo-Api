"""Provide versioned keyed digests and opaque token generation."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping

DIGEST_ALGORITHM = "hmac-sha256"
DEFAULT_TOKEN_BYTES = 32


class DigestConfigurationError(ValueError):
    """Report invalid digest key configuration."""


class DigestService:
    """Create and verify self-describing HMAC-SHA256 digests."""

    def __init__(
        self,
        *,
        active_key_id: str,
        peppers: Mapping[str, bytes],
    ) -> None:
        """Initialize digest keys.

        Args:
            active_key_id: Key ID used for new digests.
            peppers: Available key IDs and secret pepper bytes.

        Raises:
            DigestConfigurationError: If active key configuration is invalid.
        """
        if active_key_id not in peppers or not peppers[active_key_id]:
            raise DigestConfigurationError("Active digest key is unavailable.")
        self._active_key_id = active_key_id
        self._peppers = dict(peppers)

    def digest(self, purpose: str, *parts: str) -> str:
        """Create a self-describing digest.

        Args:
            purpose: Domain separation purpose.
            parts: Ordered values to authenticate.

        Returns:
            Encoded digest containing key ID and algorithm.
        """
        payload = _encode_payload(purpose, parts)
        raw_digest = hmac.new(
            self._peppers[self._active_key_id],
            payload,
            hashlib.sha256,
        ).digest()
        encoded = base64.urlsafe_b64encode(raw_digest).rstrip(b"=").decode()
        return f"{self._active_key_id}${DIGEST_ALGORITHM}${encoded}"

    def verify(self, encoded: str, purpose: str, *parts: str) -> bool:
        """Verify a digest using its declared key version.

        Args:
            encoded: Self-describing stored digest.
            purpose: Expected domain separation purpose.
            parts: Ordered candidate values.

        Returns:
            Whether the digest is valid.
        """
        metadata = _parse_digest(encoded)
        if metadata is None:
            return False
        key_id, algorithm = metadata
        if algorithm != DIGEST_ALGORITHM or key_id not in self._peppers:
            return False
        payload = _encode_payload(purpose, parts)
        raw_digest = hmac.new(
            self._peppers[key_id],
            payload,
            hashlib.sha256,
        ).digest()
        candidate = base64.urlsafe_b64encode(raw_digest).rstrip(b"=").decode()
        return hmac.compare_digest(encoded, f"{key_id}${algorithm}${candidate}")

    def needs_rotation(self, encoded: str) -> bool:
        """Return whether a digest uses an older configured key.

        Args:
            encoded: Self-describing stored digest.

        Returns:
            Whether the digest should be rewritten.
        """
        metadata = _parse_digest(encoded)
        return metadata is not None and metadata[0] != self._active_key_id


def generate_opaque_token(entropy_bytes: int = DEFAULT_TOKEN_BYTES) -> str:
    """Generate a URL-safe token with cryptographic entropy.

    Args:
        entropy_bytes: Number of random bytes.

    Returns:
        URL-safe opaque token.

    Raises:
        ValueError: If fewer than 32 bytes are requested.
    """
    if entropy_bytes < DEFAULT_TOKEN_BYTES:
        raise ValueError("Opaque tokens require at least 256 bits of entropy.")
    return secrets.token_urlsafe(entropy_bytes)


def _encode_payload(purpose: str, parts: tuple[str, ...]) -> bytes:
    encoded_parts = [purpose.encode("utf-8")]
    encoded_parts.extend(part.encode("utf-8") for part in parts)
    return b"".join(
        len(part).to_bytes(4, "big") + part
        for part in encoded_parts
    )


def _parse_digest(encoded: str) -> tuple[str, str] | None:
    pieces = encoded.split("$", 2)
    if len(pieces) != 3 or not all(pieces):
        return None
    return pieces[0], pieces[1]
