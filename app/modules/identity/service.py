"""Apply canonical account creation and lifecycle rules."""

from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.identifiers import (
    IdentifierValidationError,
    new_uuid,
    normalize_email,
    normalize_phone_e164,
)
from app.modules.identity.models import AuthIdentity, UserAccount
from app.modules.identity.types import AccountStatus, AuthProvider


class AccountRepositoryProtocol(Protocol):
    """Describe persistence required by the account service."""

    def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by ID."""
        ...

    def identifier_exists(
        self,
        email: str | None,
        phone_e164: str | None,
    ) -> bool:
        """Return whether an identifier already exists."""
        ...

    def add(self, account: UserAccount) -> None:
        """Stage an account."""
        ...

    def add_identity(self, identity: AuthIdentity) -> None:
        """Stage an authentication identity."""
        ...

    def flush(self) -> None:
        """Flush staged changes without committing."""
        ...


class AccountServiceError(Exception):
    """Represent a safe account-domain error."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize a safe domain error.

        Args:
            code: Stable machine-readable error code.
            message: PII-free diagnostic message.
        """
        self.code = code
        super().__init__(message)


class AccountValidationError(AccountServiceError):
    """Report invalid account input or state."""


class AccountConflictError(AccountServiceError):
    """Report an account uniqueness conflict safely."""


class AccountNotFoundError(AccountServiceError):
    """Report an unavailable account without exposing identifiers."""


class AccountService:
    """Manage canonical user accounts without committing transactions."""

    def __init__(self, repository: AccountRepositoryProtocol) -> None:
        """Initialize the account service.

        Args:
            repository: Account persistence implementation.
        """
        self.repository = repository

    def create_pending_local_account(
        self,
        *,
        email: str,
        phone_e164: str | None,
        password_hash: str,
    ) -> UserAccount:
        """Create a pending account and local authentication identity.

        Args:
            email: Required email address.
            phone_e164: Optional phone number.
            password_hash: Precomputed password hash.

        Returns:
            Staged pending account.

        Raises:
            AccountValidationError: If required credentials are missing or
                invalid.
            AccountConflictError: If an identifier is already registered.
        """
        if not email.strip() or not password_hash:
            raise AccountValidationError(
                "IAM_REGISTRATION_CREDENTIALS_REQUIRED",
                "Email and password credentials are required.",
            )
        try:
            normalized_email = normalize_email(email)
            normalized_phone = (
                normalize_phone_e164(phone_e164)
                if phone_e164 is not None
                else None
            )
        except IdentifierValidationError as exc:
            raise AccountValidationError(
                "IAM_IDENTIFIER_INVALID",
                "An account identifier is invalid.",
            ) from exc
        if self.repository.identifier_exists(normalized_email, normalized_phone):
            raise AccountConflictError(
                "IAM_IDENTIFIER_CONFLICT",
                "An account with the supplied identifier already exists.",
            )
        user_id = new_uuid()
        account = UserAccount(
            user_id=user_id,
            email=normalized_email,
            phone_e164=normalized_phone,
            password_hash=password_hash,
            status=AccountStatus.PENDING,
        )
        identity = AuthIdentity(
            auth_identity_id=new_uuid(),
            user_id=user_id,
            provider=AuthProvider.LOCAL,
            provider_subject=str(user_id),
        )
        self.repository.add(account)
        self.repository.add_identity(identity)
        try:
            self.repository.flush()
        except IntegrityError:
            raise AccountConflictError(
                "IAM_IDENTIFIER_CONFLICT",
                "An account with the supplied identifier already exists.",
            ) from None
        return account

    def get_account(self, user_id: UUID) -> UserAccount:
        """Return an account by ID.

        Args:
            user_id: Stable account identifier.

        Returns:
            Matching account.

        Raises:
            AccountNotFoundError: If no account exists.
        """
        account = self.repository.get_by_id(user_id)
        if account is None:
            raise AccountNotFoundError(
                "IAM_ACCOUNT_NOT_FOUND",
                "Account is unavailable.",
            )
        return account

    def assert_account_can_authenticate(self, account: UserAccount) -> None:
        """Require an active account for authentication.

        Args:
            account: Account being authenticated.

        Raises:
            AccountValidationError: If the account is not active.
        """
        if account.status != AccountStatus.ACTIVE:
            raise AccountValidationError(
                "IAM_ACCOUNT_UNAVAILABLE",
                "Account cannot authenticate.",
            )
