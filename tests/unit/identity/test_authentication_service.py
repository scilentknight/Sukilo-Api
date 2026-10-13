"""Test login rate limits and credential handling."""

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
from app.modules.identity.service import AuthenticationService
from app.modules.identity.types import AccountStatus


class AccountRepositoryFake:
    """Return a fixed account for login lookups."""

    def __init__(self, account: UserAccount | None) -> None:
        """Store the optional account."""
        self.account = account

    def get_by_email(self, email: str, *, for_update: bool = False):
        """Return the account when emails match."""
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
    """Reject unexpected session creation during limit tests."""

    def create(self, **kwargs):
        """Fail if login incorrectly succeeds."""
        raise AssertionError(f"Unexpected session create: {kwargs}")


def test_login_rate_limit_blocks_credential_stuffing() -> None:
    """Reject excess password attempts for one identifier."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    digests = DigestService(active_key_id="v1", peppers={"v1": b"pepper"})
    limiter = InMemoryRateLimiter(clock=lambda: now)
    passwords = PasswordHasher()
    account = UserAccount(
        user_id=UUID(int=1),
        email="buyer@example.com",
        status=AccountStatus.ACTIVE,
        password_hash=passwords.hash_password("correct-password"),
        auth_version=1,
    )
    service = AuthenticationService(
        account_repository=AccountRepositoryFake(account),
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
    with pytest.raises(RateLimitExceeded) as error:
        service.login(
            identifier="buyer@example.com",
            password="wrong-password",
            country_code=None,
            device_id=None,
            device_name=None,
            ip_hash=None,
            user_agent_hash=None,
        )
    assert error.value.retry_after_seconds == 900
