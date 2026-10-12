"""Test refresh-session rotation and replay response."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.security import DigestService
from app.modules.identity.models import UserAccount
from app.modules.identity.sessions import SessionError, SessionService
from app.modules.identity.types import AccountStatus


class SessionRepositoryFake:
    """Store refresh generations by token digest."""

    def __init__(self) -> None:
        """Initialize session storage."""
        self.rows = {}

    def add(self, session):
        """Store a session."""
        self.rows[session.refresh_token_hash] = session

    def get_by_refresh_hash_for_update(self, token_hash):
        """Return a session by token digest."""
        return self.rows.get(token_hash)

    def revoke_family(self, family_id, now, reason):
        """Revoke every generation in a family."""
        for row in self.rows.values():
            if row.family_id == family_id and row.revoked_at is None:
                row.revoked_at = now
                row.revoke_reason = reason

    def get_active_owned(self, user_id, session_id, now):
        """Return a live owned session."""
        return next(
            (
                row
                for row in self.rows.values()
                if row.user_id == user_id
                and row.session_id == session_id
                and row.revoked_at is None
                and row.rotated_at is None
                and row.expires_at > now
            ),
            None,
        )

    def flush(self):
        """Accept staged changes."""


class AccountRepositoryFake:
    """Return one account for refresh validation."""

    def __init__(self, account) -> None:
        """Store the account."""
        self.account = account

    def get_by_id(self, user_id, *, for_update=False):
        """Return the account when IDs match."""
        return self.account if self.account.user_id == user_id else None


class TokenServiceFake:
    """Return deterministic access tokens."""

    def issue_access_token(self, **kwargs):
        """Return a token containing the session ID."""
        return f"access-{kwargs['session_id']}"


def test_refresh_rotates_and_replay_revokes_family() -> None:
    """Create one replacement and invalidate a replayed family."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    account = UserAccount(
        user_id=UUID(int=1),
        status=AccountStatus.ACTIVE,
        auth_version=1,
    )
    repository = SessionRepositoryFake()
    service = SessionService(
        repository=repository,
        account_repository=AccountRepositoryFake(account),
        digests=DigestService(active_key_id="v1", peppers={"v1": b"pepper"}),
        tokens=TokenServiceFake(),
        clock=lambda: now,
        refresh_ttl=timedelta(days=30),
        token_source=iter(("first-refresh", "second-refresh")).__next__,
    )
    first = service.create(
        account=account,
        authentication_methods=("password",),
        authenticated_at=now,
        device_id="device-1",
        device_name="Laptop",
        ip_hash=None,
        user_agent_hash=None,
    )
    second = service.rotate(first.refresh_token)
    assert second.refresh_token == "second-refresh"
    with pytest.raises(SessionError, match="replay"):
        service.rotate(first.refresh_token)
    assert account.auth_version == 2
    assert all(row.revoked_at == now for row in repository.rows.values())


def test_logout_invalidates_session_for_access_validation() -> None:
    """Reject bearer sessions immediately after refresh-session logout."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    account = UserAccount(
        user_id=UUID(int=1),
        status=AccountStatus.ACTIVE,
        auth_version=1,
    )
    repository = SessionRepositoryFake()
    service = SessionService(
        repository=repository,
        account_repository=AccountRepositoryFake(account),
        digests=DigestService(active_key_id="v1", peppers={"v1": b"pepper"}),
        tokens=TokenServiceFake(),
        clock=lambda: now,
        refresh_ttl=timedelta(days=30),
        token_source=lambda: "refresh-token",
    )
    pair = service.create(
        account=account,
        authentication_methods=("password",),
        authenticated_at=now,
        device_id=None,
        device_name=None,
        ip_hash=None,
        user_agent_hash=None,
    )
    service.assert_active(account.user_id, pair.session_id)
    service.revoke_by_refresh_token(pair.refresh_token)
    with pytest.raises(SessionError):
        service.assert_active(account.user_id, pair.session_id)


class RevokeAllRepositoryFake(SessionRepositoryFake):
    """Capture bulk revocation arguments."""

    def __init__(self) -> None:
        """Initialize empty capture."""
        super().__init__()
        self.revocations = []

    def revoke_all_by_user(self, user_id, now, reason, except_session_id):
        """Record one bulk revocation."""
        self.revocations.append((user_id, now, reason, except_session_id))


class SecurityNotificationFake:
    """Capture security notifications."""

    def __init__(self) -> None:
        """Initialize empty deliveries."""
        self.security = []

    def send_security(self, notification):
        """Capture one notification."""
        self.security.append(notification)


def _revoke_all_service(account, notifications):
    repository = RevokeAllRepositoryFake()
    service = SessionService(
        repository=repository,
        account_repository=AccountRepositoryFake(account),
        digests=DigestService(active_key_id="v1", peppers={"v1": b"pepper"}),
        tokens=TokenServiceFake(),
        clock=lambda: datetime(2026, 10, 12, tzinfo=UTC),
        refresh_ttl=timedelta(days=30),
        notifications=notifications,
    )
    return service, repository


def test_revoke_all_can_keep_current_with_custom_reason_and_no_notice() -> None:
    """Allow callers to send their own notice and record a precise reason."""
    account = UserAccount(
        user_id=UUID(int=1),
        email="buyer@example.com",
        status=AccountStatus.ACTIVE,
        auth_version=4,
    )
    notifications = SecurityNotificationFake()
    service, repository = _revoke_all_service(account, notifications)
    service.revoke_all(
        account.user_id,
        current_session_id=UUID(int=5),
        keep_current=True,
        reason="IDENTIFIER_CHANGED",
        notify=False,
    )
    assert account.auth_version == 5
    assert repository.revocations[0][2:] == ("IDENTIFIER_CHANGED", UUID(int=5))
    assert notifications.security == []


def test_revoke_all_default_behavior_is_unchanged() -> None:
    """Keep the original reason and notification for sign-out-all."""
    account = UserAccount(
        user_id=UUID(int=1),
        email="buyer@example.com",
        status=AccountStatus.ACTIVE,
        auth_version=1,
    )
    notifications = SecurityNotificationFake()
    service, repository = _revoke_all_service(account, notifications)
    service.revoke_all(account.user_id)
    assert repository.revocations[0][2:] == ("USER_REVOKED_ALL", None)
    assert [item.event_code for item in notifications.security] == [
        "SESSIONS_REVOKED"
    ]
