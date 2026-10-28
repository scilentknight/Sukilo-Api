"""Encrypt payout account numbers before they are stored."""

from typing import Protocol

from cryptography.fernet import Fernet


class AccountNumberCipher(Protocol):
    """Encrypt a payout account number."""

    def encrypt(self, account_number: str) -> str:
        """Return ciphertext for one account number."""
        ...


class FernetAccountCipher:
    """Encrypt account numbers with a Fernet key."""

    def __init__(self, key: str) -> None:
        """Initialize the cipher.

        Args:
            key: URL-safe Fernet key.
        """
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, account_number: str) -> str:
        """Return ciphertext for one account number.

        Args:
            account_number: Raw account number.

        Returns:
            Fernet token.
        """
        return self._fernet.encrypt(account_number.encode("utf-8")).decode("ascii")
