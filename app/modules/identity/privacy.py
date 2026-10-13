"""Close authentication state without deleting the customer master."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.notifications import NotificationSender, SecurityNotification
from app.modules.identity.models import UserAccount
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.repository import (
    AccountRepository,
    AuthGrantRepository,
    OtpRepository,
    SessionRepository,
)
from app.modules.identity.types import AccountStatus, GrantPurpose
from app.ports.audit import AuditEventInput, AuditWriter

ACCOUNT_CLOSURE_ACTION = "ACCOUNT_CLOSURE"
_RECENT_PASSWORD_AGE = timedelta(minutes=15)
_CLOSE_GRANT_TTL = timedelta(minutes=15)


class AccountClosureError(Exception):
    """Report a safe account-closure failure."""


@dataclass(frozen=True, slots=True)
class ClosedIdentity:
    """Carry pre-closure delivery information outside persisted state."""

    previous_email: str | None


@dataclass(frozen=True, slots=True)
class PendingIdentityClosure:
    """Carry locked identity state through one closure transaction."""

    account: UserAccount
    previous_email: str | None


class IdentityPrivacyService:
    """Manage account, credential, and authentication closure state."""

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        grant_repository: AuthGrantRepository,
        otp_repository: OtpRepository,
        session_repository: SessionRepository,
        grants: AuthGrantService,
        passwords: PasswordHasher,
        digests: DigestService,
        notifications: NotificationSender,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
        events: SecurityEventRecorder | None = None,
    ) -> None:
        """Initialize identity privacy dependencies."""
        self.accounts = accounts
        self.grant_repository = grant_repository
        self.otp_repository = otp_repository
        self.session_repository = session_repository
        self.grants = grants
        self.passwords = passwords
        self.digests = digests
        self.notifications = notifications
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock
        self.events = events

    def request_closure(
        self,
        actor: AuthContext,
        password: str,
        step_up_grant: str,
    ) -> str:
        """Consume closure step-up and issue one confirmation grant."""
        account = self.accounts.get_by_id(actor.user_id, for_update=True)
        now = self.clock()
        if (
            account is None
            or account.status != AccountStatus.ACTIVE
            or account.password_hash is None
            or "password" not in actor.authentication_methods
            or now - actor.authenticated_at > _RECENT_PASSWORD_AGE
            or not self.passwords.verify_password(password, account.password_hash).valid
        ):
            raise AccountClosureError("Account closure is unavailable.")
        self.grants.consume(
            step_up_grant,
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.STEP_UP,
            action_class=ACCOUNT_CLOSURE_ACTION,
            subject_hash=None,
        )
        grant = self.grants.create(
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.CLOSE_ACCOUNT,
            action_class=ACCOUNT_CLOSURE_ACTION,
            subject_hash=None,
            authentication_methods=[*actor.authentication_methods, "otp"],
            correlation_id=actor.correlation_id,
            ttl=_CLOSE_GRANT_TTL,
        )
        self._audit("iam.account.closure_requested", actor)
        return grant

    def close_identity(self, actor: AuthContext, close_grant: str) -> ClosedIdentity:
        """Consume confirmation and close all authentication state."""
        pending = self.begin_closure(actor, close_grant)
        return self.finish_closure(actor, pending)

    def begin_closure(
        self,
        actor: AuthContext,
        close_grant: str,
    ) -> PendingIdentityClosure:
        """Lock the account and consume confirmation before policy checks."""
        account = self.accounts.get_by_id(actor.user_id, for_update=True)
        if account is None or account.status != AccountStatus.ACTIVE:
            raise AccountClosureError("Account closure is unavailable.")
        self.grants.consume(
            close_grant,
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.CLOSE_ACCOUNT,
            action_class=ACCOUNT_CLOSURE_ACTION,
            subject_hash=None,
        )
        return PendingIdentityClosure(
            account=account,
            previous_email=account.email,
        )

    def finish_closure(
        self,
        actor: AuthContext,
        pending: PendingIdentityClosure,
    ) -> ClosedIdentity:
        """Apply irreversible identity changes after policy approval."""
        now = self.clock()
        account = pending.account
        account.email = self._tombstone(actor.user_id, "email", 40)
        account.phone_e164 = self._tombstone(actor.user_id, "phone", 24)
        account.email_verified_at = None
        account.phone_verified_at = None
        account.password_hash = None
        account.status = AccountStatus.CLOSED
        account.closed_at = now
        account.auth_version += 1
        account.version += 1
        self.accounts.delete_identities_by_user(actor.user_id)
        self.session_repository.revoke_all_by_user(
            actor.user_id,
            now,
            "ACCOUNT_CLOSED",
            None,
        )
        self.otp_repository.invalidate_all_by_user(actor.user_id, now)
        self.grant_repository.revoke_all_by_user(
            actor.user_id,
            now,
        )
        self.accounts.flush()
        self._audit("iam.account.closed", actor)
        return ClosedIdentity(previous_email=pending.previous_email)

    def send_confirmation(self, closed: ClosedIdentity) -> None:
        """Queue a generic closure confirmation to the previous destination."""
        if closed.previous_email is not None:
            self.notifications.send_security(
                SecurityNotification(
                    destination=closed.previous_email,
                    event_code="ACCOUNT_CLOSED",
                    locale_code="en",
                )
            )

    def _tombstone(self, user_id: UUID, kind: str, maximum: int) -> str:
        digest = self.digests.digest(
            "account-closure-tombstone",
            str(user_id),
            kind,
        ).rsplit("$", 1)[-1]
        return f"closed-{kind}-{digest}"[:maximum]

    def _audit(self, action: str, actor: AuthContext) -> None:
        self.audit_writer.stage(
            AuditEventInput(
                action=action,
                resource_type="user_account",
                resource_id=actor.user_id,
                correlation_id=actor.correlation_id,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                after_data={"status": action.rsplit(".", 1)[-1]},
            ),
            self.transaction,
        )
        if self.events is not None:
            self.events.record_signal(action, actor.correlation_id)
