"""Replace verified account identifiers through identity ownership proofs."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.auth_context import AuthContext
from app.core.identifiers import (
    IdentifierValidationError,
    normalize_email,
    normalize_phone_e164,
)
from app.core.transactions import DatabaseTransaction
from app.modules.customers.identity_ports import (
    CandidateOtpPort,
    ChangeGrantPort,
    DestinationDigestPort,
    IdentifierAccountPort,
    SessionRevocationPort,
)
from app.modules.customers.service import CustomerServiceError
from app.modules.identity.grants import AuthGrantError
from app.modules.identity.identifier_change import (
    EMAIL_CHANGE_BINDING,
    PHONE_CHANGE_BINDING,
    IdentifierChangeBinding,
)
from app.modules.identity.models import UserAccount
from app.modules.identity.notifications import (
    NotificationSender,
    SecurityNotification,
)
from app.modules.identity.types import AccountStatus
from app.ports.audit import AuditEventInput, AuditWriter
from app.ports.country import CountryReader, CountryReference

RECENT_PASSWORD_WINDOW = timedelta(minutes=5)
OLD_DESTINATION_EVENT = "IDENTIFIER_CHANGED"
NEW_DESTINATION_EVENT = "IDENTIFIER_CHANGE_CONFIRMED"
CONFLICT_MESSAGE = "Identifier change cannot be completed."


class IdentifierChangeError(CustomerServiceError):
    """Report a safe identifier-change failure with a stable code."""

    code = "CUSTOMER_IDENTIFIER_CHANGE_FAILED"


class IdentifierChangeValidationError(IdentifierChangeError):
    """Report malformed or unsupported identifier-change input."""

    code = "CUSTOMER_IDENTIFIER_CHANGE_INVALID"


class IdentifierChangeUnavailableError(IdentifierChangeError):
    """Report missing recent authentication or an unusable account."""

    code = "CUSTOMER_IDENTIFIER_CHANGE_UNAVAILABLE"


class IdentifierChangeInvalidError(IdentifierChangeError):
    """Report an unusable, replayed, or mis-bound authorization grant."""

    code = "CUSTOMER_IDENTIFIER_GRANT_INVALID"


class IdentifierChangeConflictError(IdentifierChangeError):
    """Report a duplicate identifier without revealing its owner."""

    code = "CUSTOMER_IDENTIFIER_CONFLICT"


@dataclass(frozen=True, slots=True)
class RequestIdentifierChange:
    """Describe the start of one identifier replacement."""

    actor: AuthContext
    new_email: str | None
    new_phone: str | None
    country_code: str
    locale_code: str


@dataclass(frozen=True, slots=True)
class ConfirmIdentifierChange:
    """Describe the final replacement using an opaque verified grant."""

    actor: AuthContext
    new_email: str | None
    new_phone: str | None
    grant: str
    country_code: str | None = None
    locale_code: str = "en"


@dataclass(frozen=True, slots=True)
class _Candidate:
    binding: IdentifierChangeBinding
    value: str

    @property
    def is_email(self) -> bool:
        return self.binding is EMAIL_CHANGE_BINDING


class IdentifierChangeService:
    """Start and confirm verified email or phone replacement."""

    def __init__(
        self,
        *,
        accounts: IdentifierAccountPort,
        otp: CandidateOtpPort,
        grants: ChangeGrantPort,
        digests: DestinationDigestPort,
        sessions: SessionRevocationPort,
        notifications: NotificationSender,
        countries: CountryReader,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize identity and persistence collaborators."""
        self.accounts = accounts
        self.otp = otp
        self.grants = grants
        self.digests = digests
        self.sessions = sessions
        self.notifications = notifications
        self.countries = countries
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock

    def request_change(self, command: RequestIdentifierChange) -> UUID:
        """Send an owned verification OTP to one candidate identifier.

        Args:
            command: Authenticated request with exactly one candidate.

        Returns:
            Opaque OTP challenge identifier.

        Raises:
            IdentifierChangeUnavailableError: If recent password authentication
                or an active account is missing.
            IdentifierChangeValidationError: If input or market is invalid.
            IdentifierChangeConflictError: If the candidate is already used.
        """
        self._require_recent_password(command.actor)
        self._load_active_account(command.actor.user_id)
        country = self._resolve_country(command.country_code)
        if not self.countries.is_operation_enabled(
            country.country_id,
            "CUSTOMER_IDENTIFIER_CHANGE",
        ):
            raise IdentifierChangeValidationError("Market is unavailable.")
        if not self.countries.is_locale_enabled(
            country.country_id,
            command.locale_code,
        ):
            raise IdentifierChangeValidationError("Market locale is unavailable.")
        candidate = _parse_candidate(
            command.new_email,
            command.new_phone,
            country.code,
        )
        self._reject_duplicate(candidate)
        return self.otp.request(
            user_id=command.actor.user_id,
            destination=candidate.value,
            channel=candidate.binding.channel,
            purpose=candidate.binding.otp_purpose,
            locale_code=command.locale_code,
        )

    def confirm_change(self, command: ConfirmIdentifierChange) -> None:
        """Consume a matching grant and replace one verified identifier.

        Args:
            command: Authenticated candidate and opaque grant.

        Raises:
            IdentifierChangeValidationError: If the candidate is invalid.
            IdentifierChangeUnavailableError: If the account is unusable.
            IdentifierChangeInvalidError: If the grant is not valid for this
                user, session, candidate, and change type.
            IdentifierChangeConflictError: If the candidate is already used.
        """
        region = (
            self._resolve_country(command.country_code).code
            if command.country_code
            else None
        )
        candidate = _parse_candidate(command.new_email, command.new_phone, region)
        account = self._load_active_account(command.actor.user_id)
        self._consume_grant(command.actor, command.grant, candidate)
        self._reject_duplicate(candidate)
        previous = _replace_identifier(account, candidate, self.clock())
        self._flush_or_conflict()
        self.sessions.revoke_all(
            account.user_id,
            current_session_id=command.actor.session_id,
            keep_current=True,
            reason="IDENTIFIER_CHANGED",
            notify=False,
        )
        self._stage_audit(command.actor, candidate)
        self._notify(previous, candidate, command.locale_code)

    def _require_recent_password(self, actor: AuthContext) -> None:
        age = self.clock() - actor.authenticated_at
        if (
            "password" not in actor.authentication_methods
            or age < timedelta(0)
            or age > RECENT_PASSWORD_WINDOW
        ):
            raise IdentifierChangeUnavailableError(
                "Recent password authentication is required."
            )

    def _load_active_account(self, user_id: UUID) -> UserAccount:
        account = self.accounts.get_by_id(user_id, for_update=True)
        if account is None or account.status != AccountStatus.ACTIVE:
            raise IdentifierChangeUnavailableError("Account is unavailable.")
        return account

    def _resolve_country(self, code: str) -> CountryReference:
        try:
            return self.countries.resolve_country(code)
        except (KeyError, ValueError) as error:
            raise IdentifierChangeValidationError("Market is unavailable.") from error

    def _reject_duplicate(self, candidate: _Candidate) -> None:
        existing = (
            self.accounts.get_by_email(candidate.value)
            if candidate.is_email
            else self.accounts.get_by_phone(candidate.value)
        )
        if existing is not None:
            raise IdentifierChangeConflictError(CONFLICT_MESSAGE)

    def _consume_grant(
        self,
        actor: AuthContext,
        raw_grant: str,
        candidate: _Candidate,
    ) -> None:
        try:
            self.grants.consume(
                raw_grant,
                user_id=actor.user_id,
                session_id=actor.session_id,
                purpose=candidate.binding.grant_purpose,
                action_class=candidate.binding.action_class,
                subject_hash=self.digests.digest("otp-destination", candidate.value),
            )
        except AuthGrantError as error:
            raise IdentifierChangeInvalidError("Authorization grant is invalid.") from error

    def _flush_or_conflict(self) -> None:
        try:
            self.accounts.flush()
        except IntegrityError:
            raise IdentifierChangeConflictError(CONFLICT_MESSAGE) from None

    def _stage_audit(self, actor: AuthContext, candidate: _Candidate) -> None:
        self.audit_writer.stage(
            AuditEventInput(
                action="iam.identifier.changed",
                resource_type="user_account",
                resource_id=actor.user_id,
                correlation_id=actor.correlation_id,
                actor_user_id=actor.user_id,
                after_data={
                    "identifier_type": "EMAIL" if candidate.is_email else "PHONE",
                    "changed_fields": list(_changed_fields(candidate)),
                    "other_sessions_revoked": True,
                },
            ),
            self.transaction,
        )

    def _notify(
        self,
        previous: str | None,
        candidate: _Candidate,
        locale_code: str,
    ) -> None:
        if previous is not None:
            self.notifications.send_security(
                SecurityNotification(previous, OLD_DESTINATION_EVENT, locale_code)
            )
        self.notifications.send_security(
            SecurityNotification(candidate.value, NEW_DESTINATION_EVENT, locale_code)
        )


def _parse_candidate(
    new_email: str | None,
    new_phone: str | None,
    default_region: str | None,
) -> _Candidate:
    email = new_email.strip() if new_email is not None else ""
    phone = new_phone.strip() if new_phone is not None else ""
    if bool(email) == bool(phone):
        raise IdentifierChangeValidationError("Exactly one identifier is required.")
    try:
        if email:
            return _Candidate(EMAIL_CHANGE_BINDING, normalize_email(email))
        return _Candidate(
            PHONE_CHANGE_BINDING,
            normalize_phone_e164(phone, default_region),
        )
    except IdentifierValidationError as error:
        raise IdentifierChangeValidationError("Identifier is invalid.") from error


def _replace_identifier(
    account: UserAccount,
    candidate: _Candidate,
    now: datetime,
) -> str | None:
    if candidate.is_email:
        previous = account.email
        account.email = candidate.value
        account.email_verified_at = now
    else:
        previous = account.phone_e164
        account.phone_e164 = candidate.value
        account.phone_verified_at = now
    account.version += 1
    return previous


def _changed_fields(candidate: _Candidate) -> tuple[str, str]:
    if candidate.is_email:
        return ("email", "email_verified_at")
    return ("phone_e164", "phone_verified_at")
