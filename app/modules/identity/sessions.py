"""Manage rotating refresh-token sessions and ownership operations."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.core.security import DigestService, generate_opaque_token
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.models import UserAccount, UserSession
from app.modules.identity.notifications import (
    NotificationSender,
    SecurityNotification,
)
from app.modules.identity.types import AccountStatus


class SessionRepositoryProtocol(Protocol):
    """Describe refresh-session persistence."""

    def add(self, session: UserSession) -> None:
        """Stage a session."""
        ...

    def get_by_refresh_hash_for_update(
        self,
        token_hash: str,
    ) -> UserSession | None:
        """Lock a session by refresh digest."""
        ...

    def revoke_family(
        self,
        family_id: UUID,
        now: datetime,
        reason: str,
    ) -> None:
        """Revoke all active rows in one family."""
        ...

    def list_active_by_user(
        self,
        user_id: UUID,
        now: datetime,
    ) -> list[UserSession]:
        """Return active sessions owned by an account."""
        ...

    def get_owned_for_update(
        self,
        user_id: UUID,
        session_id: UUID,
    ) -> UserSession | None:
        """Lock an owned session."""
        ...

    def get_active_owned(
        self,
        user_id: UUID,
        session_id: UUID,
        now: datetime,
    ) -> UserSession | None:
        """Return an active session owned by an account."""
        ...

    def revoke_all_by_user(
        self,
        user_id: UUID,
        now: datetime,
        reason: str,
        except_session_id: UUID | None,
    ) -> None:
        """Revoke sessions owned by an account."""
        ...

    def flush(self) -> None:
        """Flush staged changes."""
        ...


class SessionAccountRepositoryProtocol(Protocol):
    """Describe account locking required during refresh."""

    def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        """Return an account by ID."""
        ...


class AccessTokenIssuer(Protocol):
    """Issue access tokens for authenticated sessions."""

    def issue_access_token(
        self,
        *,
        user_id: UUID,
        session_id: UUID,
        auth_version: int,
        authentication_methods: tuple[str, ...],
        authenticated_at: datetime,
    ) -> str:
        """Issue one access token."""
        ...


@dataclass(frozen=True, slots=True)
class TokenPair:
    """Return access and one-time refresh credentials."""

    access_token: str
    refresh_token: str
    session_id: UUID


class SessionError(Exception):
    """Report a generic session or refresh failure."""


class SessionService:
    """Create, rotate, and revoke server-side refresh sessions."""

    def __init__(
        self,
        *,
        repository: SessionRepositoryProtocol,
        account_repository: SessionAccountRepositoryProtocol,
        digests: DigestService,
        tokens: AccessTokenIssuer,
        clock: Callable[[], datetime],
        refresh_ttl: timedelta,
        token_source: Callable[[], str] = generate_opaque_token,
        events: SecurityEventRecorder | None = None,
        notifications: NotificationSender | None = None,
    ) -> None:
        """Initialize session dependencies and lifetime."""
        self.repository = repository
        self.account_repository = account_repository
        self.digests = digests
        self.tokens = tokens
        self.clock = clock
        self.refresh_ttl = refresh_ttl
        self.token_source = token_source
        self.events = events
        self.notifications = notifications

    def create(
        self,
        *,
        account: UserAccount,
        authentication_methods: tuple[str, ...],
        authenticated_at: datetime,
        device_id: str | None,
        device_name: str | None,
        ip_hash: str | None,
        user_agent_hash: str | None,
    ) -> TokenPair:
        """Create a new refresh family and access token."""
        pair = self._create_generation(
            account=account,
            family_id=uuid4(),
            authentication_methods=authentication_methods,
            authenticated_at=authenticated_at,
            device_id=device_id,
            device_name=device_name,
            ip_hash=ip_hash,
            user_agent_hash=user_agent_hash,
        )
        if self.events is not None:
            self.events.record(
                action="iam.session.created",
                resource_type="user_session",
                resource_id=pair.session_id,
                actor_user_id=account.user_id,
            )
        self._notify(account, "NEW_SESSION")
        return pair

    def rotate(self, raw_refresh_token: str) -> TokenPair:
        """Rotate one refresh token or revoke its family on replay."""
        token_hash = self.digests.digest("refresh-token", raw_refresh_token)
        current = self.repository.get_by_refresh_hash_for_update(token_hash)
        now = self.clock()
        if current is None or current.revoked_at is not None:
            raise SessionError("Refresh session is unavailable.")
        account = self.account_repository.get_by_id(
            current.user_id,
            for_update=True,
        )
        if account is None or account.status != AccountStatus.ACTIVE:
            raise SessionError("Refresh session is unavailable.")
        if current.rotated_at is not None:
            self.repository.revoke_family(
                current.family_id,
                now,
                "REFRESH_REPLAY",
            )
            account.auth_version += 1
            if self.events is not None:
                self.events.record(
                    action="iam.session.refresh_reuse_detected",
                    resource_type="user_session_family",
                    resource_id=current.family_id,
                    actor_user_id=account.user_id,
                )
            self._notify(account, "REFRESH_REPLAY")
            self.repository.flush()
            raise SessionError("Refresh token replay detected.")
        if current.expires_at <= now:
            raise SessionError("Refresh session is unavailable.")
        replacement = self._create_generation(
            account=account,
            family_id=current.family_id,
            authentication_methods=("refresh",),
            authenticated_at=current.issued_at,
            device_id=current.device_id,
            device_name=current.device_name,
            ip_hash=current.ip_hash,
            user_agent_hash=current.user_agent_hash,
        )
        current.rotated_at = now
        current.last_used_at = now
        current.replaced_by_session_id = replacement.session_id
        self.repository.flush()
        return replacement

    def list_owned(self, user_id: UUID) -> list[UserSession]:
        """Return active sessions owned by one account."""
        return self.repository.list_active_by_user(user_id, self.clock())

    def assert_active(self, user_id: UUID, session_id: UUID) -> None:
        """Require a live session for bearer-token validation."""
        if (
            self.repository.get_active_owned(
                user_id,
                session_id,
                self.clock(),
            )
            is None
        ):
            raise SessionError("Refresh session is unavailable.")

    def revoke_by_refresh_token(self, raw_refresh_token: str) -> None:
        """Revoke the current refresh generation when it exists."""
        token_hash = self.digests.digest("refresh-token", raw_refresh_token)
        session = self.repository.get_by_refresh_hash_for_update(token_hash)
        if session is not None and session.revoked_at is None:
            session.revoked_at = self.clock()
            session.revoke_reason = "LOGOUT"
            self.repository.flush()

    def revoke_owned(self, user_id: UUID, session_id: UUID) -> None:
        """Revoke one session only when owned by the requesting account."""
        session = self.repository.get_owned_for_update(user_id, session_id)
        if session is None:
            raise SessionError("Refresh session is unavailable.")
        if session.revoked_at is None:
            session.revoked_at = self.clock()
            session.revoke_reason = "USER_REVOKED"
            self.repository.flush()

    def revoke_all(
        self,
        user_id: UUID,
        *,
        current_session_id: UUID | None = None,
        keep_current: bool = False,
        reason: str = "USER_REVOKED_ALL",
        notify: bool = True,
    ) -> None:
        """Revoke owned sessions and invalidate prior access tokens.

        Args:
            user_id: Account whose sessions are revoked.
            current_session_id: Session preserved when `keep_current` is set.
            keep_current: Whether to preserve `current_session_id`.
            reason: PII-free revocation reason persisted on revoked rows.
            notify: Whether to queue the generic sessions-revoked notice.

        Raises:
            SessionError: If the account is unavailable.
        """
        account = self.account_repository.get_by_id(user_id, for_update=True)
        if account is None:
            raise SessionError("Account is unavailable.")
        exception = current_session_id if keep_current else None
        self.repository.revoke_all_by_user(
            user_id,
            self.clock(),
            reason,
            exception,
        )
        account.auth_version += 1
        if self.events is not None:
            self.events.record(
                action="iam.session.revoked_all",
                resource_type="user_account",
                resource_id=user_id,
                actor_user_id=user_id,
            )
        if notify:
            self._notify(account, "SESSIONS_REVOKED")
        self.repository.flush()

    def _notify(self, account: UserAccount, event_code: str) -> None:
        if self.notifications is None or account.email is None:
            return
        self.notifications.send_security(
            SecurityNotification(
                destination=account.email,
                event_code=event_code,
                locale_code="en",
            )
        )

    def _create_generation(
        self,
        *,
        account: UserAccount,
        family_id: UUID,
        authentication_methods: tuple[str, ...],
        authenticated_at: datetime,
        device_id: str | None,
        device_name: str | None,
        ip_hash: str | None,
        user_agent_hash: str | None,
    ) -> TokenPair:
        now = self.clock()
        raw_refresh_token = self.token_source()
        session = UserSession(
            session_id=uuid4(),
            family_id=family_id,
            user_id=account.user_id,
            refresh_token_hash=self.digests.digest(
                "refresh-token",
                raw_refresh_token,
            ),
            device_id=device_id,
            device_name=device_name,
            ip_hash=ip_hash,
            user_agent_hash=user_agent_hash,
            issued_at=now,
            expires_at=now + self.refresh_ttl,
            created_at=now,
        )
        self.repository.add(session)
        self.repository.flush()
        access_token = self.tokens.issue_access_token(
            user_id=account.user_id,
            session_id=session.session_id,
            auth_version=account.auth_version,
            authentication_methods=authentication_methods,
            authenticated_at=authenticated_at,
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            session_id=session.session_id,
        )
