"""Test pending registration overwrite and active identifier conflicts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.modules.identity.models import UserAccount
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.service import RegistrationService
from app.modules.identity.types import AccountStatus, OtpChannel, OtpPurpose
from app.ports.country import CountryReference


class CountryReaderFake:
    """Allow registration in a single test market."""

    def resolve_country(self, code: str) -> CountryReference:
        """Return a stable country for any code."""
        return CountryReference(
            country_id=UUID(int=42),
            code=code,
            status="ACTIVE",
        )

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Enable registration only."""
        del country_id
        return operation == "REGISTER"

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Allow the supplied locale."""
        del country_id, locale_code
        return True


class AccountRepositoryFake:
    """Store one account keyed by email for registration flows."""

    def __init__(self, account: UserAccount | None = None) -> None:
        """Initialize optional existing state."""
        self.account = account
        self.flushed = False

    def get_by_email(self, email: str, *, for_update: bool = False):
        """Return the stored account when emails match."""
        del for_update
        if self.account is None or self.account.email != email:
            return None
        return self.account

    def flush(self) -> None:
        """Mark that staged changes were flushed."""
        self.flushed = True


class AccountServiceFake:
    """Reject unexpected account creation during overwrite tests."""

    def create_pending_local_account(self, **kwargs):
        """Fail if a new account is incorrectly created."""
        raise AssertionError(f"Unexpected account create: {kwargs}")


class OtpServiceFake:
    """Record OTP requests issued by registration."""

    def __init__(self) -> None:
        """Initialize an empty request log."""
        self.requests: list[dict[str, object]] = []
        self.challenge_id = uuid4()

    def request(self, **kwargs) -> UUID:
        """Record and acknowledge one OTP request."""
        self.requests.append(kwargs)
        return self.challenge_id


def test_pending_registration_overwrites_password_and_resends_otp() -> None:
    """Allow legitimate owners to reclaim squatted pending emails."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    passwords = PasswordHasher()
    pending = UserAccount(
        user_id=UUID(int=7),
        email="buyer@example.com",
        status=AccountStatus.PENDING,
        password_hash=passwords.hash_password("attacker-password"),
        auth_version=1,
    )
    repository = AccountRepositoryFake(pending)
    otp = OtpServiceFake()
    service = RegistrationService(
        accounts=AccountServiceFake(),
        account_repository=repository,
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
    assert repository.flushed is True
    assert passwords.verify_password(
        "owner-password-12",
        pending.password_hash,
    ).valid
    assert not passwords.verify_password(
        "attacker-password",
        pending.password_hash,
    ).valid
    assert otp.requests == [
        {
            "user_id": pending.user_id,
            "destination": "buyer@example.com",
            "channel": OtpChannel.EMAIL,
            "purpose": OtpPurpose.REGISTER,
            "locale_code": "en-US",
        }
    ]


def test_active_registration_conflict_returns_decoy_challenge() -> None:
    """Hide active-account conflicts behind a decoy challenge id."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    passwords = PasswordHasher()
    active = UserAccount(
        user_id=UUID(int=8),
        email="buyer@example.com",
        status=AccountStatus.ACTIVE,
        password_hash=passwords.hash_password("owner-password-12"),
        auth_version=1,
    )
    repository = AccountRepositoryFake(active)
    otp = OtpServiceFake()
    service = RegistrationService(
        accounts=AccountServiceFake(),
        account_repository=repository,
        passwords=passwords,
        otp=otp,
        countries=CountryReaderFake(),
        clock=lambda: now,
    )
    challenge_id = service.register(
        email="buyer@example.com",
        phone_e164=None,
        password="attacker-password",
        country_code="NP",
        locale_code="en-US",
    )
    assert challenge_id != otp.challenge_id
    assert otp.requests == []
    assert passwords.verify_password(
        "owner-password-12",
        active.password_hash,
    ).valid
