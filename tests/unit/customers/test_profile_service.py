"""Test the authenticated customer profile projection."""

from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.modules.customers.profile import (
    ProfileService,
    ProfileUnavailableError,
    mask_email,
    mask_phone,
)
from app.modules.customers.service import PreferenceView
from app.modules.identity.models import UserAccount, UserSession
from app.modules.identity.types import AccountStatus

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
OTHER_SESSION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)
PASSWORD_HASH = "$argon2id$secret-hash"
FORBIDDEN_FIELDS = {
    "password_hash",
    "auth_version",
    "roles",
    "role_assignments",
    "consents",
    "consent_history",
    "refresh_token_hash",
    "ip_hash",
    "user_agent_hash",
    "version",
}


def _actor(session_id: UUID = SESSION_ID) -> AuthContext:
    return AuthContext(
        user_id=USER_ID,
        session_id=session_id,
        auth_version=7,
        authentication_methods=("password",),
        authenticated_at=NOW,
        correlation_id=UUID(int=9),
    )


def _account(**overrides) -> UserAccount:
    values = {
        "user_id": USER_ID,
        "email": "buyer@example.com",
        "phone_e164": "+9779812345678",
        "password_hash": PASSWORD_HASH,
        "email_verified_at": NOW - timedelta(days=2),
        "phone_verified_at": None,
        "status": AccountStatus.ACTIVE,
        "auth_version": 7,
        "last_login_at": NOW - timedelta(hours=1),
        "created_at": NOW - timedelta(days=30),
        "updated_at": NOW - timedelta(days=1),
        "version": 3,
    }
    values.update(overrides)
    return UserAccount(**values)


def _session(session_id: UUID = SESSION_ID) -> UserSession:
    return UserSession(
        session_id=session_id,
        family_id=UUID(int=20),
        user_id=USER_ID,
        refresh_token_hash="v1$hmac-sha256$refresh-secret",
        device_id="device-1",
        device_name="Pixel",
        ip_hash="v1$hmac-sha256$ip-secret",
        user_agent_hash="v1$hmac-sha256$agent-secret",
        issued_at=NOW - timedelta(minutes=3),
        expires_at=NOW + timedelta(days=30),
        created_at=NOW - timedelta(minutes=3),
    )


class AccountsFake:
    """Return one account by ID."""

    def __init__(self, account: UserAccount | None) -> None:
        """Store the optional account."""
        self.account = account

    def get_by_id(self, user_id: UUID, *, for_update: bool = False):
        """Return the account when IDs match."""
        del for_update
        if self.account is not None and self.account.user_id == user_id:
            return self.account
        return None


class PreferencesFake:
    """Return an optional preference projection."""

    def __init__(self, view: PreferenceView | None) -> None:
        """Store the optional view."""
        self.view = view
        self.actors: list[AuthContext] = []

    def get_preferences(self, actor: AuthContext):
        """Capture the actor and return the view."""
        self.actors.append(actor)
        return self.view


class SessionsFake:
    """Return live sessions owned by the account."""

    def __init__(self, rows: list[UserSession]) -> None:
        """Store live rows."""
        self.rows = rows

    def list_owned(self, user_id: UUID) -> list[UserSession]:
        """Return rows only for the expected owner."""
        return self.rows if user_id == USER_ID else []


def _preference() -> PreferenceView:
    return PreferenceView(
        user_id=USER_ID,
        country_id=COUNTRY_ID,
        country_code="NP",
        locale_code="en-NP",
        currency_code="NPR",
        marketing_email=True,
        marketing_sms=False,
        version=4,
    )


def _service(
    account: UserAccount | None = None,
    *,
    preference: PreferenceView | None = None,
    sessions: list[UserSession] | None = None,
) -> tuple[ProfileService, PreferencesFake]:
    preferences = PreferencesFake(preference)
    service = ProfileService(
        accounts=AccountsFake(account if account is not None else _account()),
        preferences=preferences,
        sessions=SessionsFake([_session()] if sessions is None else sessions),
    )
    return service, preferences


def test_profile_projects_masked_identifiers_flags_and_timestamps() -> None:
    """Return only the safe account projection."""
    service, preferences = _service(preference=_preference())
    profile = service.get_profile(_actor())
    assert profile.user_id == USER_ID
    assert profile.status == AccountStatus.ACTIVE
    assert profile.email_masked == "b***@example.com"
    assert profile.phone_masked == "+**********678"
    assert profile.email_verified is True
    assert profile.phone_verified is False
    assert profile.created_at == NOW - timedelta(days=30)
    assert profile.last_login_at == NOW - timedelta(hours=1)
    assert profile.preferences == _preference()
    assert preferences.actors == [_actor()]


def test_profile_identifies_current_session_without_raw_metadata() -> None:
    """Expose the active session and device indicator only."""
    service, _ = _service(sessions=[_session(OTHER_SESSION_ID), _session()])
    profile = service.get_profile(_actor())
    assert profile.current_session.session_id == SESSION_ID
    assert profile.current_session.is_current is True
    assert profile.current_session.device_id == "device-1"
    assert profile.current_session.device_name == "Pixel"
    assert profile.current_session.issued_at == NOW - timedelta(minutes=3)


def test_profile_never_exposes_sensitive_or_raw_values() -> None:
    """Keep credentials, versions, grants, and raw identifiers out of output."""
    service, _ = _service(preference=_preference())
    profile = service.get_profile(_actor())
    names = {field.name for field in fields(profile)}
    session_names = {field.name for field in fields(profile.current_session)}
    assert not names & FORBIDDEN_FIELDS
    assert not session_names & FORBIDDEN_FIELDS
    rendered = repr(asdict(profile))
    for secret in (
        PASSWORD_HASH,
        "buyer@example.com",
        "9812345678",
        "refresh-secret",
        "ip-secret",
        "agent-secret",
    ):
        assert secret not in rendered


def test_profile_handles_missing_identifiers_and_preferences() -> None:
    """Represent absent identifiers and preferences explicitly."""
    account = _account(
        email=None,
        phone_e164=None,
        email_verified_at=None,
        last_login_at=None,
    )
    service, _ = _service(account)
    profile = service.get_profile(_actor())
    assert profile.email_masked is None
    assert profile.phone_masked is None
    assert profile.email_verified is False
    assert profile.phone_verified is False
    assert profile.last_login_at is None
    assert profile.preferences is None


@pytest.mark.parametrize(
    "status",
    [AccountStatus.PENDING, AccountStatus.SUSPENDED, AccountStatus.CLOSED],
)
def test_profile_requires_active_account(status: AccountStatus) -> None:
    """Hide profiles for non-active accounts."""
    service, _ = _service(_account(status=status))
    with pytest.raises(ProfileUnavailableError):
        service.get_profile(_actor())


def test_profile_requires_existing_account_and_live_current_session() -> None:
    """Fail closed when the account or current session is unavailable."""
    missing = ProfileService(
        accounts=AccountsFake(None),
        preferences=PreferencesFake(None),
        sessions=SessionsFake([_session()]),
    )
    with pytest.raises(ProfileUnavailableError):
        missing.get_profile(_actor())
    service, _ = _service(sessions=[_session(OTHER_SESSION_ID)])
    with pytest.raises(ProfileUnavailableError):
        service.get_profile(_actor())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a@example.com", "*@example.com"),
        ("ab@example.com", "a***@example.com"),
        ("Buyer@Example.com", "B***@Example.com"),
    ],
)
def test_email_mask_hides_local_part(value: str, expected: str) -> None:
    """Mask every local-part character after the first."""
    assert mask_email(value) == expected


def test_phone_mask_keeps_only_plus_and_last_digits() -> None:
    """Mask a phone number without leaking its country or subscriber prefix."""
    assert mask_phone("+9779812345678") == "+**********678"
    assert mask_phone("+1234") == "+*234"
