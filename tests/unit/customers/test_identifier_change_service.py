"""Test verified identifier replacement rules."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.customers.identifier_change import (
    ConfirmIdentifierChange,
    IdentifierChangeConflictError,
    IdentifierChangeInvalidError,
    IdentifierChangeService,
    IdentifierChangeUnavailableError,
    IdentifierChangeValidationError,
    RequestIdentifierChange,
)
from app.modules.identity.application import IdentityApplication
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.otp import OtpService
from app.modules.identity.models import UserAccount
from app.modules.identity.notifications import SecurityNotification
from app.modules.identity.types import (
    AccountStatus,
    GrantPurpose,
    OtpChannel,
    OtpPurpose,
)
from app.ports.audit import AuditEventInput
from app.ports.country import AddressPolicy, CountryReference
from tests.fakes.iam import InMemoryCountryReader, RecordingAuditWriter

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
OTHER_USER_ID = UUID(int=50)
SESSION_ID = UUID(int=2)
OTHER_SESSION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)
DIGESTS = DigestService(active_key_id="v1", peppers={"v1": b"pepper"})
NEW_EMAIL = "new@example.com"
NEW_PHONE = "+9779801234567"


def _actor(
    *,
    methods: tuple[str, ...] = ("password",),
    authenticated_at: datetime = NOW - timedelta(minutes=1),
    session_id: UUID = SESSION_ID,
) -> AuthContext:
    return AuthContext(
        user_id=USER_ID,
        session_id=session_id,
        auth_version=1,
        authentication_methods=methods,
        authenticated_at=authenticated_at,
        correlation_id=UUID(int=9),
    )


def _account(user_id: UUID = USER_ID, **overrides) -> UserAccount:
    values = {
        "user_id": user_id,
        "email": "old@example.com" if user_id == USER_ID else "other@example.com",
        "phone_e164": "+9779812345678" if user_id == USER_ID else "+9779800000000",
        "status": AccountStatus.ACTIVE,
        "auth_version": 1,
        "version": 1,
        "email_verified_at": NOW - timedelta(days=9),
        "phone_verified_at": NOW - timedelta(days=9),
    }
    values.update(overrides)
    return UserAccount(**values)


class AccountsFake:
    """Store accounts and optionally simulate a unique-index race."""

    def __init__(self, accounts: list[UserAccount], events: list[str]) -> None:
        """Index accounts by ID."""
        self.rows = {row.user_id: row for row in accounts}
        self.events = events
        self.locked: list[UUID] = []
        self.flush_error: Exception | None = None

    def get_by_id(self, user_id: UUID, *, for_update: bool = False):
        """Return an account and record row locks."""
        if for_update:
            self.locked.append(user_id)
        return self.rows.get(user_id)

    def get_by_email(self, email: str, *, for_update: bool = False):
        """Return an account by normalized email."""
        del for_update
        return next((r for r in self.rows.values() if r.email == email), None)

    def get_by_phone(self, phone_e164: str):
        """Return an account by normalized phone."""
        return next((r for r in self.rows.values() if r.phone_e164 == phone_e164), None)

    def flush(self) -> None:
        """Record a flush or raise the configured database error."""
        if self.flush_error is not None:
            raise self.flush_error
        self.events.append("flush")


class GrantRepositoryFake:
    """Store grants by protected token digest."""

    def __init__(self) -> None:
        """Initialize empty storage."""
        self.rows = {}

    def add(self, grant):
        """Store a grant."""
        self.rows[grant.token_hash] = grant

    def get_by_token_hash_for_update(self, token_hash):
        """Return a grant by digest."""
        return self.rows.get(token_hash)

    def flush(self):
        """Accept staged changes."""


@dataclass
class OtpFake:
    """Capture candidate OTP requests."""

    calls: list[dict[str, object]] = field(default_factory=list)

    def request(self, **kwargs) -> UUID:
        """Capture arguments and return a challenge ID."""
        self.calls.append(kwargs)
        return UUID(int=1234)


class SessionsFake:
    """Mimic session revocation and the auth-version increment."""

    def __init__(self, accounts: AccountsFake, events: list[str]) -> None:
        """Store collaborators."""
        self.accounts = accounts
        self.events = events
        self.calls: list[tuple[UUID, dict[str, object]]] = []

    def revoke_all(self, user_id: UUID, **kwargs) -> None:
        """Record the call and increment the locked account version."""
        self.calls.append((user_id, kwargs))
        self.accounts.rows[user_id].auth_version += 1
        self.events.append("revoke")


class NotificationsFake:
    """Capture security notifications in order."""

    def __init__(self, events: list[str]) -> None:
        """Initialize capture."""
        self.security: list[SecurityNotification] = []
        self.events = events

    def send_security(self, notification: SecurityNotification) -> None:
        """Capture one security notification."""
        self.security.append(notification)
        self.events.append("notify")


class AuditFake(RecordingAuditWriter):
    """Record redacted audit events and event order."""

    def __init__(self, events: list[str]) -> None:
        """Initialize capture."""
        super().__init__()
        self.events = events

    def stage(self, event: AuditEventInput, transaction: DatabaseTransaction) -> None:
        """Record one event."""
        super().stage(event, transaction)
        self.events.append("audit")


@dataclass
class Harness:
    """Bundle service collaborators for assertions."""

    service: IdentifierChangeService
    account: UserAccount
    accounts: AccountsFake
    otp: OtpFake
    grants: AuthGrantService
    sessions: SessionsFake
    notifications: NotificationsFake
    audit: AuditFake
    transaction: DatabaseTransaction
    events: list[str]
    clock: list[datetime]


def _countries() -> InMemoryCountryReader:
    countries = InMemoryCountryReader()
    countries.add_country(
        CountryReference(COUNTRY_ID, "NP", "ACTIVE"),
        operations={"CUSTOMER_IDENTIFIER_CHANGE"},
        locales={"en-NP"},
        currencies={"NPR"},
        geography_ids=set(),
        address_policy=AddressPolicy(False, False, True),
        policy_versions={},
    )
    return countries


def _harness(
    *,
    account: UserAccount | None = None,
    others: list[UserAccount] | None = None,
) -> Harness:
    events: list[str] = []
    account = account or _account()
    accounts = AccountsFake([account, *(others or [])], events)
    clock = [NOW]
    grants = AuthGrantService(
        repository=GrantRepositoryFake(),
        digests=DIGESTS,
        clock=lambda: clock[0],
        token_source=iter(f"opaque-grant-token-{n}" for n in range(50)).__next__,
    )
    otp, sessions = OtpFake(), SessionsFake(accounts, events)
    notifications, audit = NotificationsFake(events), AuditFake(events)
    transaction = DatabaseTransaction(session=object())
    service = IdentifierChangeService(
        accounts=accounts,
        otp=otp,
        grants=grants,
        digests=DIGESTS,
        sessions=sessions,
        notifications=notifications,
        countries=_countries(),
        audit_writer=audit,
        transaction=transaction,
        clock=lambda: clock[0],
    )
    return Harness(
        service, account, accounts, otp, grants, sessions,
        notifications, audit, transaction, events, clock,
    )


def _request(**overrides) -> RequestIdentifierChange:
    values = {
        "actor": _actor(),
        "new_email": NEW_EMAIL,
        "new_phone": None,
        "country_code": "NP",
        "locale_code": "en-NP",
    }
    values.update(overrides)
    return RequestIdentifierChange(**values)


def _grant(
    harness: Harness,
    destination: str,
    *,
    purpose: GrantPurpose = GrantPurpose.CHANGE_EMAIL,
    action_class: str = "email.change",
    user_id: UUID = USER_ID,
    session_id: UUID = SESSION_ID,
) -> str:
    return harness.grants.create(
        user_id=user_id,
        session_id=session_id,
        purpose=purpose,
        action_class=action_class,
        subject_hash=DIGESTS.digest("otp-destination", destination),
        authentication_methods=["password", "otp"],
        correlation_id=UUID(int=9),
        ttl=timedelta(minutes=5),
    )


def _confirm(grant: str, **overrides) -> ConfirmIdentifierChange:
    values = {
        "actor": _actor(),
        "new_email": NEW_EMAIL,
        "new_phone": None,
        "grant": grant,
    }
    values.update(overrides)
    return ConfirmIdentifierChange(**values)


def test_email_request_creates_owned_candidate_otp_without_applying() -> None:
    """Send an owned VERIFY_EMAIL OTP to the normalized candidate only."""
    harness = _harness()
    challenge_id = harness.service.request_change(
        _request(new_email="  New@Example.COM ")
    )
    assert challenge_id == UUID(int=1234)
    assert harness.otp.calls == [
        {
            "user_id": USER_ID,
            "destination": NEW_EMAIL,
            "channel": OtpChannel.EMAIL,
            "purpose": OtpPurpose.VERIFY_EMAIL,
            "locale_code": "en-NP",
        }
    ]
    assert harness.account.email == "old@example.com"
    assert harness.account.auth_version == 1
    assert harness.accounts.locked == [USER_ID]


def test_phone_request_normalizes_national_number_with_country_region() -> None:
    """Send an owned VERIFY_PHONE OTP over SMS to the E.164 candidate."""
    harness = _harness()
    harness.service.request_change(
        _request(new_email=None, new_phone="9801234567")
    )
    call = harness.otp.calls[0]
    assert call["destination"] == NEW_PHONE
    assert call["channel"] == OtpChannel.SMS
    assert call["purpose"] == OtpPurpose.VERIFY_PHONE
    assert harness.account.phone_e164 == "+9779812345678"


@pytest.mark.parametrize(
    "overrides",
    [
        {"new_email": None, "new_phone": None},
        {"new_email": NEW_EMAIL, "new_phone": NEW_PHONE},
        {"new_email": "", "new_phone": None},
        {"new_email": None, "new_phone": "   "},
        {"new_email": "not-an-email"},
        {"new_email": None, "new_phone": "123"},
        {"locale_code": "ne-NP"},
        {"country_code": "ZZ"},
    ],
)
def test_request_rejects_invalid_candidates_and_markets(overrides) -> None:
    """Require exactly one valid candidate in an enabled market locale."""
    harness = _harness()
    with pytest.raises(IdentifierChangeValidationError):
        harness.service.request_change(_request(**overrides))
    assert harness.otp.calls == []


def test_request_requires_recent_password_authentication() -> None:
    """Reject missing, stale, future, and non-password authentication."""
    harness = _harness()
    stale = _actor(authenticated_at=NOW - timedelta(minutes=5, seconds=1))
    future = _actor(authenticated_at=NOW + timedelta(seconds=1))
    refreshed = _actor(methods=("refresh",))
    otp_only = _actor(methods=("otp",))
    for actor in (stale, future, refreshed, otp_only):
        with pytest.raises(IdentifierChangeUnavailableError):
            harness.service.request_change(_request(actor=actor))
    assert harness.otp.calls == []
    boundary = _actor(authenticated_at=NOW - timedelta(minutes=5))
    harness.service.request_change(_request(actor=boundary))
    assert len(harness.otp.calls) == 1


@pytest.mark.parametrize(
    "status",
    [AccountStatus.PENDING, AccountStatus.SUSPENDED, AccountStatus.CLOSED],
)
def test_request_requires_active_account(status: AccountStatus) -> None:
    """Reject non-active accounts before any challenge is created."""
    harness = _harness(account=_account(status=status))
    with pytest.raises(IdentifierChangeUnavailableError):
        harness.service.request_change(_request())
    assert harness.otp.calls == []


def test_request_requires_identifier_change_operation() -> None:
    """Reject markets that disable customer identifier changes."""
    countries = _countries()
    countries.add_country(
        CountryReference(UUID(int=7), "AE", "ACTIVE"),
        operations=set(),
        locales={"ar-AE"},
        currencies={"AED"},
        geography_ids=set(),
        address_policy=AddressPolicy(False, False, True),
        policy_versions={},
    )
    harness = _harness()
    harness.service.countries = countries
    with pytest.raises(IdentifierChangeValidationError):
        harness.service.request_change(_request(country_code="AE", locale_code="ar-AE"))
    assert harness.otp.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"new_email": "OTHER@example.com"},
        {"new_email": "old@example.com"},
        {"new_email": None, "new_phone": "+9779800000000"},
        {"new_email": None, "new_phone": "+9779812345678"},
    ],
)
def test_request_rejects_duplicates_with_one_safe_conflict(overrides) -> None:
    """Reject another account's and unchanged identifiers identically."""
    harness = _harness(others=[_account(OTHER_USER_ID)])
    with pytest.raises(IdentifierChangeConflictError) as error:
        harness.service.request_change(_request(**overrides))
    assert str(error.value) == "Identifier change cannot be completed."
    assert harness.otp.calls == []


def test_confirm_replaces_email_and_revokes_other_sessions() -> None:
    """Atomically replace email, verification time, and session trust."""
    harness = _harness()
    grant = _grant(harness, NEW_EMAIL)
    harness.service.confirm_change(_confirm(grant, locale_code="en-NP"))
    assert harness.account.email == NEW_EMAIL
    assert harness.account.email_verified_at == NOW
    assert harness.account.phone_e164 == "+9779812345678"
    assert harness.account.phone_verified_at == NOW - timedelta(days=9)
    assert harness.account.auth_version == 2
    assert harness.account.version == 2
    assert harness.accounts.locked[0] == USER_ID
    assert harness.sessions.calls == [
        (
            USER_ID,
            {
                "current_session_id": SESSION_ID,
                "keep_current": True,
                "reason": "IDENTIFIER_CHANGED",
                "notify": False,
            },
        )
    ]


def test_confirm_replaces_phone_without_touching_email() -> None:
    """Apply a phone change with its own purpose and verification time."""
    harness = _harness()
    grant = _grant(
        harness,
        NEW_PHONE,
        purpose=GrantPurpose.CHANGE_PHONE,
        action_class="phone.change",
    )
    harness.service.confirm_change(
        _confirm(grant, new_email=None, new_phone="9801234567", country_code="NP")
    )
    assert harness.account.phone_e164 == NEW_PHONE
    assert harness.account.phone_verified_at == NOW
    assert harness.account.email == "old@example.com"
    assert harness.account.email_verified_at == NOW - timedelta(days=9)
    assert harness.account.auth_version == 2


def test_confirm_stages_redacted_audit_then_queues_notifications() -> None:
    """Persist and audit before notifying old and new destinations."""
    harness = _harness()
    harness.service.confirm_change(_confirm(_grant(harness, NEW_EMAIL)))
    assert harness.events == ["flush", "revoke", "audit", "notify", "notify"]
    event, transaction = harness.audit.records[0]
    assert transaction is harness.transaction
    assert event.action == "iam.identifier.changed"
    assert event.resource_type == "user_account"
    assert event.resource_id == USER_ID
    assert event.actor_user_id == USER_ID
    assert event.correlation_id == UUID(int=9)
    assert event.after_data == {
        "identifier_type": "EMAIL",
        "changed_fields": ["email", "email_verified_at"],
        "other_sessions_revoked": True,
    }
    assert [
        (item.destination, item.event_code)
        for item in harness.notifications.security
    ] == [
        ("old@example.com", "IDENTIFIER_CHANGED"),
        (NEW_EMAIL, "IDENTIFIER_CHANGE_CONFIRMED"),
    ]
    rendered = repr(harness.audit.records[0][0])
    assert NEW_EMAIL not in rendered and "old@example.com" not in rendered


def test_phone_change_audit_and_notifications_use_phone_values() -> None:
    """Keep phone values out of audit while notifying both phone numbers."""
    harness = _harness()
    grant = _grant(
        harness,
        NEW_PHONE,
        purpose=GrantPurpose.CHANGE_PHONE,
        action_class="phone.change",
    )
    harness.service.confirm_change(
        _confirm(grant, new_email=None, new_phone=NEW_PHONE)
    )
    event, _ = harness.audit.records[0]
    assert event.after_data["identifier_type"] == "PHONE"
    assert event.after_data["changed_fields"] == [
        "phone_e164",
        "phone_verified_at",
    ]
    assert "+977" not in repr(event)
    assert [item.destination for item in harness.notifications.security] == [
        "+9779812345678",
        NEW_PHONE,
    ]


def test_confirm_adds_identifier_when_none_existed() -> None:
    """Notify only the new destination when no prior value existed."""
    harness = _harness(account=_account(phone_e164=None, phone_verified_at=None))
    grant = _grant(
        harness,
        NEW_PHONE,
        purpose=GrantPurpose.CHANGE_PHONE,
        action_class="phone.change",
    )
    harness.service.confirm_change(
        _confirm(grant, new_email=None, new_phone=NEW_PHONE)
    )
    assert harness.account.phone_e164 == NEW_PHONE
    assert [item.destination for item in harness.notifications.security] == [
        NEW_PHONE
    ]


def test_grant_replay_is_denied_and_does_not_change_state_again() -> None:
    """Allow a successful grant exactly once."""
    harness = _harness()
    grant = _grant(harness, NEW_EMAIL)
    harness.service.confirm_change(_confirm(grant))
    with pytest.raises(IdentifierChangeInvalidError):
        harness.service.confirm_change(_confirm(grant))
    assert harness.account.auth_version == 2
    assert len(harness.audit.records) == 1
    assert len(harness.notifications.security) == 2


def test_candidate_substitution_is_denied_and_grant_remains_usable() -> None:
    """Bind the grant to the OTP-verified candidate digest."""
    harness = _harness()
    grant = _grant(harness, NEW_EMAIL)
    with pytest.raises(IdentifierChangeInvalidError):
        harness.service.confirm_change(
            _confirm(grant, new_email="attacker@example.com")
        )
    assert harness.account.email == "old@example.com"
    assert harness.sessions.calls == []
    assert harness.notifications.security == []
    harness.service.confirm_change(_confirm(grant))
    assert harness.account.email == NEW_EMAIL


@pytest.mark.parametrize(
    "grant_kwargs",
    [
        {"purpose": GrantPurpose.CHANGE_PHONE},
        {"action_class": "phone.change"},
        {"action_class": "step_up"},
        {"session_id": OTHER_SESSION_ID},
        {"user_id": OTHER_USER_ID},
        {"purpose": GrantPurpose.STEP_UP},
    ],
)
def test_confirm_rejects_wrong_purpose_action_session_or_user(
    grant_kwargs,
) -> None:
    """Require the exact user, session, purpose, and action class."""
    harness = _harness(others=[_account(OTHER_USER_ID)])
    grant = _grant(harness, NEW_EMAIL, **grant_kwargs)
    with pytest.raises(IdentifierChangeInvalidError):
        harness.service.confirm_change(_confirm(grant))
    assert harness.account.email == "old@example.com"
    assert harness.sessions.calls == []


def test_confirm_rejects_unknown_and_expired_grants() -> None:
    """Treat missing and expired grants as the same invalid grant."""
    harness = _harness()
    with pytest.raises(IdentifierChangeInvalidError):
        harness.service.confirm_change(_confirm("never-issued-grant-token"))
    grant = _grant(harness, NEW_EMAIL)
    harness.clock[0] = NOW + timedelta(minutes=5)
    with pytest.raises(IdentifierChangeInvalidError):
        harness.service.confirm_change(_confirm(grant))
    assert harness.account.email == "old@example.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"new_email": None, "new_phone": None},
        {"new_email": NEW_EMAIL, "new_phone": NEW_PHONE},
        {"new_email": "", "new_phone": None},
        {"new_email": "not-an-email"},
    ],
)
def test_confirm_rejects_removal_and_ambiguous_candidates(overrides) -> None:
    """Never remove or replace two identifiers in one confirmation."""
    harness = _harness()
    grant = _grant(harness, NEW_EMAIL)
    with pytest.raises(IdentifierChangeValidationError):
        harness.service.confirm_change(_confirm(grant, **overrides))
    assert harness.account.email == "old@example.com"
    assert harness.account.phone_e164 == "+9779812345678"


def test_confirm_duplicate_maps_safe_conflict_without_side_effects() -> None:
    """Reject a candidate claimed after OTP verification."""
    harness = _harness(
        others=[_account(OTHER_USER_ID, email=NEW_EMAIL)],
    )
    grant = _grant(harness, NEW_EMAIL)
    with pytest.raises(IdentifierChangeConflictError) as error:
        harness.service.confirm_change(_confirm(grant))
    assert str(error.value) == "Identifier change cannot be completed."
    assert harness.account.email == "old@example.com"
    assert harness.sessions.calls == []
    assert harness.audit.records == []
    assert harness.notifications.security == []


def test_confirm_maps_unique_index_race_to_safe_conflict() -> None:
    """Translate a database uniqueness race without exposing details."""
    harness = _harness()
    harness.accounts.flush_error = IntegrityError("insert", {}, Exception("dup"))
    grant = _grant(harness, NEW_EMAIL)
    with pytest.raises(IdentifierChangeConflictError) as error:
        harness.service.confirm_change(_confirm(grant))
    assert str(error.value) == "Identifier change cannot be completed."
    assert harness.audit.records == []
    assert harness.notifications.security == []


@pytest.mark.parametrize(
    "status",
    [AccountStatus.PENDING, AccountStatus.SUSPENDED, AccountStatus.CLOSED],
)
def test_confirm_requires_active_locked_account(status: AccountStatus) -> None:
    """Reject non-active accounts before consuming a grant."""
    harness = _harness(account=_account(status=status))
    grant = _grant(harness, NEW_EMAIL)
    with pytest.raises(IdentifierChangeUnavailableError):
        harness.service.confirm_change(_confirm(grant))
    assert harness.accounts.locked == [USER_ID]
    assert harness.account.email == "old@example.com"


def test_confirm_rejects_unchanged_current_identifier() -> None:
    """Treat re-confirming the current identifier as a safe conflict."""
    harness = _harness()
    grant = _grant(harness, "old@example.com")
    with pytest.raises(IdentifierChangeConflictError):
        harness.service.confirm_change(_confirm(grant, new_email="old@example.com"))
    assert harness.account.auth_version == 1


class OtpRepositoryFake:
    """Store real OTP challenges for the end-to-end flow."""

    def __init__(self) -> None:
        """Initialize empty storage."""
        self.rows = {}

    def invalidate_active(self, destination_hash, purpose, now):
        """Invalidate matching live challenges."""
        for row in self.rows.values():
            if row.destination_hash == destination_hash and row.purpose == purpose:
                row.invalidated_at = now

    def add(self, challenge):
        """Store a challenge."""
        self.rows[challenge.otp_id] = challenge

    def get_for_update(self, otp_id):
        """Return a challenge."""
        return self.rows.get(otp_id)

    def flush(self):
        """Accept staged changes."""


class OtpDeliveryFake:
    """Capture raw OTP deliveries."""

    def __init__(self) -> None:
        """Initialize empty deliveries."""
        self.deliveries = []

    def send_otp(self, notification):
        """Capture one delivery."""
        self.deliveries.append(notification)


def test_request_verify_confirm_flow_uses_one_destination_digest() -> None:
    """Carry the OTP destination digest into the confirmation grant."""
    harness = _harness()
    deliveries = OtpDeliveryFake()
    otp = OtpService(
        repository=OtpRepositoryFake(),
        digests=DIGESTS,
        notifications=deliveries,
        clock=lambda: NOW,
        code_source=lambda: "654321",
        nonce_source=lambda: "nonce",
    )
    harness.service.otp = otp
    application = IdentityApplication(
        registration=None,
        authentication=None,
        recovery=None,
        sessions=None,
        otp=otp,
        grants=harness.grants,
        passwords=None,
        accounts=harness.accounts,
        tokens=None,
        digests=DIGESTS,
        notifications=deliveries,
        clock=lambda: NOW,
    )
    challenge_id = harness.service.request_change(_request(new_email="NEW@example.com"))
    assert deliveries.deliveries[0].destination == NEW_EMAIL
    grant = application.verify_identifier_change(
        context=_actor(),
        otp_id=challenge_id,
        code="654321",
        purpose=OtpPurpose.VERIFY_EMAIL,
    )
    harness.service.confirm_change(_confirm(grant))
    assert harness.account.email == NEW_EMAIL
    with pytest.raises(IdentifierChangeInvalidError):
        harness.service.confirm_change(_confirm(grant))