"""Test authentication abuse controls for Phase 2."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.security import DigestService
from app.modules.identity.models import UserAccount
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.rate_limits import (
    InMemoryRateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
)
from app.modules.identity.service import (
    AuthenticationService,
    RegistrationService,
)
from app.modules.identity.types import AccountStatus
from app.ports.country import CountryReference


class AccountRepositoryFake:
    """Provide lookup state for abuse-control tests."""

    def __init__(self, account: UserAccount | None = None) -> None:
        """Store optional account state."""
        self.account = account

    def get_by_email(self, email: str, *, for_update: bool = False):
        """Return the stored account when emails match."""
        del for_update
        if self.account is None or self.account.email != email:
            return None
        return self.account

    def get_by_phone(self, phone_e164: str):
        """Return no phone-backed account."""
        del phone_e164
        return None

    def flush(self) -> None:
        """Accept staged changes."""


class SessionServiceFake:
    """Reject unexpected session creation."""

    def create(self, **kwargs):
        """Fail if login succeeds during limit tests."""
        raise AssertionError(f"Unexpected session create: {kwargs}")


class AccountServiceFake:
    """Reject unexpected account creation."""

    def create_pending_local_account(self, **kwargs):
        """Fail if a new account is created."""
        raise AssertionError(f"Unexpected account create: {kwargs}")


class OtpServiceFake:
    """Capture OTP requests without sending mail."""

    def __init__(self) -> None:
        """Initialize request storage."""
        self.requests: list[dict[str, object]] = []
        self.challenge_id = UUID(int=99)

    def request(self, **kwargs) -> UUID:
        """Record one OTP request."""
        self.requests.append(kwargs)
        return self.challenge_id


class CountryReaderFake:
    """Allow registration operations."""

    def resolve_country(self, code: str) -> CountryReference:
        """Return a stable market reference."""
        return CountryReference(
            country_id=UUID(int=42),
            code=code,
            status="ACTIVE",
        )

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Enable registration."""
        del country_id
        return operation == "REGISTER"

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Allow the supplied locale."""
        del country_id, locale_code
        return True


def test_password_login_is_rate_limited_per_identifier() -> None:
    """Stop credential stuffing after the configured login window."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    digests = DigestService(active_key_id="v1", peppers={"v1": b"pepper"})
    limiter = InMemoryRateLimiter(clock=lambda: now)
    passwords = PasswordHasher()
    service = AuthenticationService(
        account_repository=AccountRepositoryFake(None),
        passwords=passwords,
        sessions=SessionServiceFake(),
        clock=lambda: now,
        dummy_password_hash=passwords.hash_password("dummy-password"),
        digests=digests,
        rate_limiter=limiter,
    )
    identifier_hash = digests.digest("login-identifier", "buyer@example.com")
    policy = RateLimitPolicy(limit=10, window=timedelta(minutes=15))
    for _ in range(10):
        limiter.check(f"login:identifier:{identifier_hash}", policy)
    with pytest.raises(RateLimitExceeded):
        service.login(
            identifier="Buyer@Example.com",
            password="guess",
            country_code=None,
            device_id=None,
            device_name=None,
            ip_hash=None,
            user_agent_hash=None,
        )


def test_pending_email_squatting_can_be_reclaimed() -> None:
    """Overwrite a pending registration password and resend verification."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    passwords = PasswordHasher()
    pending = UserAccount(
        user_id=UUID(int=7),
        email="buyer@example.com",
        status=AccountStatus.PENDING,
        password_hash=passwords.hash_password("attacker-password"),
        auth_version=1,
    )
    otp = OtpServiceFake()
    service = RegistrationService(
        accounts=AccountServiceFake(),
        account_repository=AccountRepositoryFake(pending),
        passwords=passwords,
        otp=otp,
        countries=CountryReaderFake(),
        clock=lambda: now,
    )
    challenge_id = service.register(
        email="buyer@example.com",
        phone_e164=None,
        password="owner-password-12",
        country_code="NP",
        locale_code="en-US",
    )
    assert challenge_id == otp.challenge_id
    assert passwords.verify_password(
        "owner-password-12",
        pending.password_hash,
    ).valid
    assert len(otp.requests) == 1
