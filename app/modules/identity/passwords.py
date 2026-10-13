"""Hash and verify passwords with an explicit Argon2id policy."""

from dataclasses import dataclass

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


class PasswordPolicyError(ValueError):
    """Report a password policy violation without exposing the password."""


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    """Describe password verification and migration state."""

    valid: bool
    needs_rehash: bool


class PasswordHasher:
    """Hash passwords with configurable Argon2id deployment parameters."""

    def __init__(
        self,
        *,
        time_cost: int = 3,
        memory_cost: int = 65536,
        parallelism: int = 4,
    ) -> None:
        """Initialize the Argon2id password hasher.

        Args:
            time_cost: Number of Argon2 iterations.
            memory_cost: Memory cost in kibibytes.
            parallelism: Number of parallel threads.
        """
        self._hasher = Argon2PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )

    def hash_password(self, password: str) -> str:
        """Validate and hash a password.

        Args:
            password: Exact password content.

        Returns:
            Encoded Argon2id hash.

        Raises:
            PasswordPolicyError: If the password length is outside policy.
        """
        self._validate_length(password)
        return self._hasher.hash(password)

    def verify_password(
        self,
        password: str,
        encoded_hash: str,
    ) -> PasswordVerification:
        """Verify a password without exposing mismatch details.

        Args:
            password: Candidate password.
            encoded_hash: Stored Argon2id hash.

        Returns:
            Verification result and rehash requirement.
        """
        try:
            valid = self._hasher.verify(encoded_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return PasswordVerification(valid=False, needs_rehash=False)
        return PasswordVerification(
            valid=valid,
            needs_rehash=self._hasher.check_needs_rehash(encoded_hash),
        )

    def _validate_length(self, password: str) -> None:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise PasswordPolicyError(
                "Password length is outside the accepted range.",
            )
