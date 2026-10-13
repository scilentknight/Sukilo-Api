"""Test account-closure privacy lifecycle behavior."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.customers.privacy import AccountClosureService, CustomerPrivacyError
from app.modules.identity.models import UserAccount
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.privacy import IdentityPrivacyService
from app.modules.identity.types import AccountStatus
from tests.fakes.iam import RecordingAuditWriter

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
CORRELATION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)


class _Accounts:
    def __init__(self, account: UserAccount) -> None:
        self.account = account
        self.identities_deleted = False

    def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserAccount | None:
        del for_update
        return self.account if user_id == self.account.user_id else None

    def delete_identities_by_user(self, user_id: UUID) -> None:
        assert user_id == self.account.user_id
        self.identities_deleted = True

    def flush(self) -> None:
        return None


class _Grants:
    def __init__(self) -> None:
        self.consumed: list[str] = []

    def consume(self, raw_token: str, **bindings: object) -> object:
        del bindings
        self.consumed.append(raw_token)
        return SimpleNamespace(grant_id=UUID(int=8))

    def create(self, **values: object) -> str:
        del values
        return "c" * 40


class _GrantRepository:
    def __init__(self) -> None:
        self.revoked = False

    def revoke_all_by_user(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.revoked = True


class _OtpRepository:
    def __init__(self) -> None:
        self.invalidated = False

    def invalidate_all_by_user(self, user_id: UUID, now: datetime) -> None:
        del user_id, now
        self.invalidated = True


class _SessionRepository:
    def __init__(self) -> None:
        self.revoked = False

    def revoke_all_by_user(self, *args: object) -> None:
        del args
        self.revoked = True


class _Notifications:
    def __init__(self) -> None:
        self.destinations: list[str] = []

    def send_security(self, notification: object) -> None:
        self.destinations.append(str(getattr(notification, "destination")))


class _HoldRetention:
    def closure_notice(self, country_id: UUID) -> str:
        del country_id
        return "notice"

    def has_pending_hold(self, user_id: UUID, country_id: UUID) -> bool:
        del user_id, country_id
        return True

    def can_pseudonymize_address(self, country_id: UUID) -> bool:
        del country_id
        return True


class _CustomerRepository:
    def list_country_ids_by_user(self, user_id: UUID) -> list[UUID]:
        del user_id
        return [COUNTRY_ID]


class _IdentityClosure:
    def __init__(self) -> None:
        self.begun = False
        self.closed = False

    def begin_closure(self, actor: AuthContext, close_grant: str) -> object:
        del actor, close_grant
        self.begun = True
        return SimpleNamespace(previous_email=None)

    def finish_closure(self, actor: AuthContext, pending: object) -> object:
        del actor, pending
        self.closed = True
        return SimpleNamespace(previous_email=None)


def _actor() -> AuthContext:
    return AuthContext(
        user_id=USER_ID,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("password",),
        authenticated_at=NOW,
        correlation_id=CORRELATION_ID,
    )


def _identity_service() -> tuple[
    IdentityPrivacyService,
    UserAccount,
    _GrantRepository,
    _OtpRepository,
    _SessionRepository,
    _Accounts,
]:
    passwords = PasswordHasher()
    account = UserAccount(
        user_id=USER_ID,
        email="buyer@example.com",
        phone_e164="+9779841234567",
        password_hash=passwords.hash_password("correct-password"),
        status=AccountStatus.ACTIVE,
        auth_version=1,
        version=1,
    )
    accounts = _Accounts(account)
    grant_repository = _GrantRepository()
    otp_repository = _OtpRepository()
    session_repository = _SessionRepository()
    service = IdentityPrivacyService(
        accounts=accounts,
        grant_repository=grant_repository,
        otp_repository=otp_repository,
        session_repository=session_repository,
        grants=_Grants(),
        passwords=passwords,
        digests=DigestService(active_key_id="v1", peppers={"v1": b"p" * 32}),
        notifications=_Notifications(),
        audit_writer=RecordingAuditWriter(),
        transaction=DatabaseTransaction(SimpleNamespace()),
        clock=lambda: NOW,
    )
    return (
        service,
        account,
        grant_repository,
        otp_repository,
        session_repository,
        accounts,
    )


def test_closure_preview_grant_does_not_close_account() -> None:
    """Leave account state unchanged until confirmation."""
    service, account, *_ = _identity_service()
    grant = service.request_closure(_actor(), "correct-password", "s" * 40)
    assert grant == "c" * 40
    assert account.status == AccountStatus.ACTIVE
    assert account.email == "buyer@example.com"


def test_confirmation_tombstones_and_revokes_authentication_state() -> None:
    """Close identity with stable non-routable tombstones and revocations."""
    service, account, grants, otp, sessions, accounts = _identity_service()
    service.close_identity(_actor(), "c" * 40)
    email_tombstone = account.email
    phone_tombstone = account.phone_e164
    assert account.status == AccountStatus.CLOSED
    assert account.password_hash is None
    assert email_tombstone is not None and "@" not in email_tombstone
    assert phone_tombstone is not None and not phone_tombstone.startswith("+")
    assert "buyer" not in email_tombstone
    assert "9841234567" not in phone_tombstone
    assert grants.revoked and otp.invalidated and sessions.revoked
    assert accounts.identities_deleted
    second, second_account, *_ = _identity_service()
    second.close_identity(_actor(), "c" * 40)
    assert second_account.email == email_tombstone
    assert second_account.phone_e164 == phone_tombstone


def test_pending_hold_blocks_closure_before_identity_mutation() -> None:
    """Reject closure while a legal or finance hold remains pending."""
    identity = _IdentityClosure()
    service = AccountClosureService(
        identity=identity,
        repository=_CustomerRepository(),
        retention=_HoldRetention(),
        countries=SimpleNamespace(),
        audit_writer=RecordingAuditWriter(),
        transaction=DatabaseTransaction(SimpleNamespace()),
        clock=lambda: NOW,
        database=SimpleNamespace(),
        context_applier=lambda database, context: None,
    )
    with pytest.raises(CustomerPrivacyError):
        service.confirm(_actor(), "c" * 40)
    assert identity.begun is True
    assert identity.closed is False
