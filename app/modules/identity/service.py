"""Apply account, registration, authentication, and recovery rules."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.core.identifiers import (
    IdentifierValidationError,
    new_uuid,
    normalize_email,
    normalize_phone_e164,
)
from app.core.security import DigestService
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.models import AuthIdentity, UserAccount
from app.modules.identity.notifications import (
    NotificationSender,
    SecurityNotification,
)
from app.modules.identity.otp import OtpService
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.rate_limits import RateLimitPolicy, RateLimiter
from app.modules.identity.sessions import SessionService, TokenPair
from app.modules.identity.types import (
    AccountStatus,
    AuthProvider,
    GrantPurpose,
    OtpChannel,
    OtpPurpose,
)
from app.ports.country import CountryReader


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

    def get_by_email(
        self,
        email: str,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by normalized email."""
        ...

    def get_by_phone(self, phone_e164: str) -> UserAccount | None:
        """Return an account by normalized phone."""
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


class RegistrationService:
    """Coordinate country-aware account registration and verification."""

    def __init__(
        self,
        *,
        accounts: AccountService,
        account_repository: AccountRepositoryProtocol,
        passwords: PasswordHasher,
        otp: OtpService,
        countries: CountryReader,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize registration dependencies."""
        self.accounts = accounts
        self.account_repository = account_repository
        self.passwords = passwords
        self.otp = otp
        self.countries = countries
        self.clock = clock

    def register(
        self,
        *,
        email: str,
        phone_e164: str | None,
        password: str,
        country_code: str,
        locale_code: str,
    ) -> UUID:
        """Create a pending account and send an email verification OTP."""
        country = self.countries.resolve_country(country_code)
        if not self.countries.is_operation_enabled(
            country.country_id,
            "REGISTER",
        ) or not self.countries.is_locale_enabled(
            country.country_id,
            locale_code,
        ):
            raise AccountValidationError(
                "IAM_MARKET_UNAVAILABLE",
                "Registration is unavailable in the selected market.",
            )
        try:
            normalized_email = normalize_email(email)
        except IdentifierValidationError as exc:
            raise AccountValidationError(
                "IAM_IDENTIFIER_INVALID",
                "An account identifier is invalid.",
            ) from exc
        password_hash = self.passwords.hash_password(password)
        existing = self.account_repository.get_by_email(
            normalized_email,
            for_update=True,
        )
        if existing is not None:
            if existing.status != AccountStatus.PENDING:
                return new_uuid()
            existing.password_hash = password_hash
            self.account_repository.flush()
            return self.otp.request(
                user_id=existing.user_id,
                destination=normalized_email,
                channel=OtpChannel.EMAIL,
                purpose=OtpPurpose.REGISTER,
                locale_code=locale_code,
            )
        try:
            account = self.accounts.create_pending_local_account(
                email=normalized_email,
                phone_e164=phone_e164,
                password_hash=password_hash,
            )
        except AccountConflictError:
            return new_uuid()
        return self.otp.request(
            user_id=account.user_id,
            destination=account.email or email,
            channel=OtpChannel.EMAIL,
            purpose=OtpPurpose.REGISTER,
            locale_code=locale_code,
        )

    def verify_registration(self, otp_id: UUID, code: str) -> UserAccount:
        """Verify registration email and activate an eligible account."""
        challenge = self.otp.verify(otp_id, OtpPurpose.REGISTER, code)
        if challenge.user_id is None:
            raise AccountValidationError(
                "IAM_REGISTRATION_INVALID",
                "Registration challenge is unavailable.",
            )
        account = self.account_repository.get_by_id(
            challenge.user_id,
            for_update=True,
        )
        if account is None or account.status != AccountStatus.PENDING:
            raise AccountValidationError(
                "IAM_REGISTRATION_INVALID",
                "Registration challenge is unavailable.",
            )
        account.email_verified_at = self.clock()
        account.status = AccountStatus.ACTIVE
        self.account_repository.flush()
        return account


class AuthenticationService:
    """Authenticate global accounts and create refresh sessions."""

    def __init__(
        self,
        *,
        account_repository: AccountRepositoryProtocol,
        passwords: PasswordHasher,
        sessions: SessionService,
        clock: Callable[[], datetime],
        dummy_password_hash: str,
        countries: CountryReader | None = None,
        digests: DigestService | None = None,
        rate_limiter: RateLimiter | None = None,
        events: SecurityEventRecorder | None = None,
    ) -> None:
        """Initialize authentication dependencies."""
        self.account_repository = account_repository
        self.passwords = passwords
        self.sessions = sessions
        self.clock = clock
        self.dummy_password_hash = dummy_password_hash
        self.countries = countries
        self.digests = digests
        self.rate_limiter = rate_limiter
        self.events = events

    def login(
        self,
        *,
        identifier: str,
        password: str,
        country_code: str | None = None,
        device_id: str | None,
        device_name: str | None,
        ip_hash: str | None,
        user_agent_hash: str | None,
    ) -> TokenPair:
        """Authenticate one identifier without revealing account existence."""
        if self.countries is not None and country_code is not None:
            country = self.countries.resolve_country(country_code)
            if not self.countries.is_operation_enabled(
                country.country_id,
                "LOGIN",
            ):
                raise AccountValidationError(
                    "IAM_MARKET_UNAVAILABLE",
                    "Authentication is unavailable in the selected market.",
                )
        self._check_login_limits(identifier, ip_hash)
        account = self._find_account(identifier)
        encoded_hash = (
            account.password_hash
            if account is not None and account.password_hash is not None
            else self.dummy_password_hash
        )
        verification = self.passwords.verify_password(password, encoded_hash)
        if (
            account is None
            or not verification.valid
            or account.status != AccountStatus.ACTIVE
        ):
            self._emit_login("iam.login.failed")
            raise AccountValidationError(
                "AUTH_INVALID_CREDENTIALS",
                "Authentication credentials are invalid.",
            )
        if verification.needs_rehash:
            account.password_hash = self.passwords.hash_password(password)
        now = self.clock()
        account.last_login_at = now
        self.account_repository.flush()
        self._emit_login("iam.login.succeeded")
        return self.sessions.create(
            account=account,
            authentication_methods=("password",),
            authenticated_at=now,
            device_id=device_id,
            device_name=device_name,
            ip_hash=ip_hash,
            user_agent_hash=user_agent_hash,
        )

    def _check_login_limits(
        self,
        identifier: str,
        ip_hash: str | None,
    ) -> None:
        if self.rate_limiter is None or self.digests is None:
            return
        identifier_hash = self.digests.digest(
            "login-identifier",
            identifier.casefold(),
        )
        policy = RateLimitPolicy(limit=10, window=timedelta(minutes=15))
        self.rate_limiter.check(f"login:identifier:{identifier_hash}", policy)
        if ip_hash is not None:
            self.rate_limiter.check(f"login:ip:{ip_hash}", policy)

    def _emit_login(self, action: str) -> None:
        """Emit a login outcome without the submitted identifier."""
        if self.events is None:
            return
        outcome = action.rsplit(".", 1)[-1].upper()
        self.events.record_signal(
            action,
            uuid4(),
            attributes={"channel": "PASSWORD", "reason_code": outcome},
        )

    def _find_account(self, identifier: str) -> UserAccount | None:
        try:
            if "@" in identifier:
                return self.account_repository.get_by_email(
                    normalize_email(identifier)
                )
            return self.account_repository.get_by_phone(
                normalize_phone_e164(identifier)
            )
        except IdentifierValidationError:
            return None


class PasswordRecoveryService:
    """Coordinate enumeration-safe password recovery and session revocation."""

    def __init__(
        self,
        *,
        account_repository: AccountRepositoryProtocol,
        passwords: PasswordHasher,
        otp: OtpService,
        grants: AuthGrantService,
        sessions: SessionService,
        notifications: NotificationSender,
        reset_ttl: timedelta,
        countries: CountryReader | None = None,
        events: SecurityEventRecorder | None = None,
    ) -> None:
        """Initialize password recovery dependencies."""
        self.account_repository = account_repository
        self.passwords = passwords
        self.otp = otp
        self.grants = grants
        self.sessions = sessions
        self.notifications = notifications
        self.reset_ttl = reset_ttl
        self.countries = countries
        self.events = events

    def request(
        self,
        *,
        email: str,
        locale_code: str,
        country_code: str | None = None,
    ) -> UUID:
        """Request password recovery without exposing account existence."""
        if self.countries is not None and country_code is not None:
            country = self.countries.resolve_country(country_code)
            if not self.countries.is_operation_enabled(
                country.country_id,
                "RESET_PASSWORD",
            ) or not self.countries.is_locale_enabled(
                country.country_id,
                locale_code,
            ):
                return new_uuid()
        try:
            normalized = normalize_email(email)
        except IdentifierValidationError:
            return new_uuid()
        account = self.account_repository.get_by_email(normalized)
        if account is None or account.status != AccountStatus.ACTIVE:
            return new_uuid()
        return self.otp.request(
            user_id=account.user_id,
            destination=normalized,
            channel=OtpChannel.EMAIL,
            purpose=OtpPurpose.RESET,
            locale_code=locale_code,
        )

    def verify(
        self,
        *,
        otp_id: UUID,
        code: str,
        correlation_id: UUID,
    ) -> str:
        """Exchange a verified reset OTP for a one-time grant."""
        challenge = self.otp.verify(otp_id, OtpPurpose.RESET, code)
        if challenge.user_id is None:
            raise AccountValidationError(
                "AUTH_RESET_INVALID",
                "Password reset is unavailable.",
            )
        return self.grants.create(
            user_id=challenge.user_id,
            session_id=None,
            purpose=GrantPurpose.RESET_PASSWORD,
            action_class="password.reset",
            subject_hash=None,
            authentication_methods=["otp"],
            correlation_id=correlation_id,
            ttl=self.reset_ttl,
        )

    def reset(
        self,
        *,
        raw_grant: str,
        new_password: str,
        locale_code: str,
    ) -> None:
        """Consume a reset grant, change password, and revoke sessions."""
        grant = self.grants.consume(
            raw_grant,
            user_id=None,
            session_id=None,
            purpose=GrantPurpose.RESET_PASSWORD,
            action_class="password.reset",
            subject_hash=None,
        )
        account = self.account_repository.get_by_id(
            grant.user_id,
            for_update=True,
        )
        if account is None or account.status != AccountStatus.ACTIVE:
            raise AccountValidationError(
                "AUTH_RESET_INVALID",
                "Password reset is unavailable.",
            )
        account.password_hash = self.passwords.hash_password(new_password)
        self.sessions.revoke_all(account.user_id)
        self.account_repository.flush()
        if self.events is not None:
            self.events.record(
                action="iam.password.changed",
                resource_type="user_account",
                resource_id=account.user_id,
                actor_user_id=account.user_id,
                after_data={"reason_code": "PASSWORD_RESET"},
            )
        if account.email is not None:
            self.notifications.send_security(
                SecurityNotification(
                    destination=account.email,
                    event_code="PASSWORD_RESET",
                    locale_code=locale_code,
                )
            )
