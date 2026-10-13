"""Test owned identifier-change OTP verification and grant issuance."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.modules.identity.application import IdentityApplication
from app.modules.identity.grants import AuthGrantError, AuthGrantService
from app.modules.identity.models import UserAccount
from app.modules.identity.otp import OtpError, OtpService
from app.modules.identity.types import (
    AccountStatus,
    GrantPurpose,
    OtpChannel,
    OtpPurpose,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
OTHER_SESSION_ID = UUID(int=3)
DIGESTS = DigestService(active_key_id="v1", peppers={"v1": b"pepper"})


class OtpRepositoryFake:
    """Store OTP challenges for deterministic verification tests."""

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


class NotificationFake:
    """Capture OTP deliveries."""

    def __init__(self) -> None:
        """Initialize empty deliveries."""
        self.deliveries = []

    def send_otp(self, notification):
        """Capture an OTP notification."""
        self.deliveries.append(notification)


class AccountRepositoryFake:
    """Return one account for state validation."""

    def __init__(self, account: UserAccount | None) -> None:
        """Store the account."""
        self.account = account

    def get_by_id(self, user_id, *, for_update=False):
        """Return the account when IDs match."""
        del for_update
        if self.account is not None and self.account.user_id == user_id:
            return self.account
        return None


def _context(user_id: UUID = USER_ID, session_id: UUID = SESSION_ID) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=session_id,
        auth_version=1,
        authentication_methods=("password",),
        authenticated_at=NOW,
        correlation_id=UUID(int=9),
    )


def _build(status: AccountStatus = AccountStatus.ACTIVE):
    otp_rows = OtpRepositoryFake()
    grant_rows = GrantRepositoryFake()
    notifications = NotificationFake()
    otp = OtpService(
        repository=otp_rows,
        digests=DIGESTS,
        notifications=notifications,
        clock=lambda: NOW,
        code_source=lambda: "123456",
        nonce_source=lambda: "nonce",
        ttl=timedelta(minutes=5),
        max_attempts=3,
    )
    grants = AuthGrantService(
        repository=grant_rows,
        digests=DIGESTS,
        clock=lambda: NOW,
        token_source=lambda: "opaque-grant-token",
    )
    account = UserAccount(user_id=USER_ID, status=status, auth_version=1)
    application = IdentityApplication(
        registration=None,
        authentication=None,
        recovery=None,
        sessions=None,
        otp=otp,
        grants=grants,
        passwords=None,
        accounts=AccountRepositoryFake(account),
        tokens=None,
        digests=DIGESTS,
        notifications=notifications,
        clock=lambda: NOW,
    )
    return application, otp, otp_rows


def _request(
    otp: OtpService,
    *,
    destination: str,
    purpose: OtpPurpose,
    channel: OtpChannel,
    user_id: UUID | None = USER_ID,
) -> UUID:
    return otp.request(
        user_id=user_id,
        destination=destination,
        channel=channel,
        purpose=purpose,
        locale_code="en-NP",
    )


def test_email_otp_issues_session_bound_change_email_grant() -> None:
    """Return a single-use grant bound to the candidate destination digest."""
    application, otp, rows = _build()
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
    )
    raw_grant = application.verify_identifier_change(
        context=_context(),
        otp_id=otp_id,
        code="123456",
        purpose=OtpPurpose.VERIFY_EMAIL,
    )
    grant = application.grants.consume(
        raw_grant,
        user_id=USER_ID,
        session_id=SESSION_ID,
        purpose=GrantPurpose.CHANGE_EMAIL,
        action_class="email.change",
        subject_hash=rows.rows[otp_id].destination_hash,
    )
    assert grant.subject_hash == DIGESTS.digest("otp-destination", "new@example.com")
    assert "otp" in grant.authentication_methods
    assert "password" in grant.authentication_methods


def test_phone_otp_issues_change_phone_grant_with_fixed_action_class() -> None:
    """Map phone ownership proof to the phone-change grant only."""
    application, otp, rows = _build()
    otp_id = _request(
        otp,
        destination="+9779812345678",
        purpose=OtpPurpose.VERIFY_PHONE,
        channel=OtpChannel.SMS,
    )
    raw_grant = application.verify_identifier_change(
        context=_context(),
        otp_id=otp_id,
        code="123456",
        purpose=OtpPurpose.VERIFY_PHONE,
    )
    with pytest.raises(AuthGrantError, match="binding"):
        application.grants.consume(
            raw_grant,
            user_id=USER_ID,
            session_id=SESSION_ID,
            purpose=GrantPurpose.CHANGE_EMAIL,
            action_class="email.change",
            subject_hash=rows.rows[otp_id].destination_hash,
        )
    application.grants.consume(
        raw_grant,
        user_id=USER_ID,
        session_id=SESSION_ID,
        purpose=GrantPurpose.CHANGE_PHONE,
        action_class="phone.change",
        subject_hash=rows.rows[otp_id].destination_hash,
    )


def test_grant_is_bound_to_verifying_session_and_single_use() -> None:
    """Reject another session and replay of the same grant."""
    application, otp, rows = _build()
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
    )
    raw_grant = application.verify_identifier_change(
        context=_context(),
        otp_id=otp_id,
        code="123456",
        purpose=OtpPurpose.VERIFY_EMAIL,
    )
    arguments = {
        "user_id": USER_ID,
        "purpose": GrantPurpose.CHANGE_EMAIL,
        "action_class": "email.change",
        "subject_hash": rows.rows[otp_id].destination_hash,
    }
    with pytest.raises(AuthGrantError, match="binding"):
        application.grants.consume(raw_grant, session_id=OTHER_SESSION_ID, **arguments)
    application.grants.consume(raw_grant, session_id=SESSION_ID, **arguments)
    with pytest.raises(AuthGrantError, match="unavailable"):
        application.grants.consume(raw_grant, session_id=SESSION_ID, **arguments)


def test_foreign_challenge_is_rejected_without_consuming_it() -> None:
    """Keep another account's OTP usable only by its owner."""
    application, otp, rows = _build()
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
        user_id=UUID(int=77),
    )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.VERIFY_EMAIL,
        )
    assert rows.rows[otp_id].verified_at is None
    assert rows.rows[otp_id].attempt_count == 0


def test_anonymous_challenge_is_rejected() -> None:
    """Never convert an unowned challenge into an identifier-change grant."""
    application, otp, _ = _build()
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
        user_id=None,
    )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.VERIFY_EMAIL,
        )


def test_wrong_purpose_and_unsupported_purpose_are_rejected() -> None:
    """Require the OTP purpose to match the change type."""
    application, otp, _ = _build()
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
    )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.VERIFY_PHONE,
        )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.RESET,
        )


def test_channel_mismatch_is_rejected() -> None:
    """Reject an email-purpose challenge delivered over SMS."""
    application, otp, _ = _build()
    otp_id = _request(
        otp,
        destination="+9779812345678",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.SMS,
    )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.VERIFY_EMAIL,
        )


def test_otp_replay_is_denied() -> None:
    """Allow one successful verification per challenge."""
    application, otp, _ = _build()
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
    )
    application.verify_identifier_change(
        context=_context(),
        otp_id=otp_id,
        code="123456",
        purpose=OtpPurpose.VERIFY_EMAIL,
    )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.VERIFY_EMAIL,
        )


def test_inactive_account_cannot_receive_identifier_grant() -> None:
    """Require an active account when exchanging ownership proof."""
    application, otp, _ = _build(AccountStatus.SUSPENDED)
    otp_id = _request(
        otp,
        destination="new@example.com",
        purpose=OtpPurpose.VERIFY_EMAIL,
        channel=OtpChannel.EMAIL,
    )
    with pytest.raises(OtpError, match="unavailable"):
        application.verify_identifier_change(
            context=_context(),
            otp_id=otp_id,
            code="123456",
            purpose=OtpPurpose.VERIFY_EMAIL,
        )
