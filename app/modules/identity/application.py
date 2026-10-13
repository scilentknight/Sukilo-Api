"""Compose identity services for thin FastAPI routes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.identifier_change import binding_for_otp_purpose
from app.modules.identity.notifications import NotificationSender
from app.modules.identity.otp import OtpError, OtpService
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.rate_limits import RateLimiter
from app.modules.identity.repository import (
    AccountRepository,
    AuthGrantRepository,
    OtpRepository,
    SessionRepository,
)
from app.modules.identity.service import (
    AccountService,
    AuthenticationService,
    PasswordRecoveryService,
    RegistrationService,
)
from app.modules.identity.security_events import (
    InMemorySecurityMetrics,
    LoggingSecurityEventSink,
    SecurityObservability,
)
from app.modules.identity.sessions import SessionService
from app.modules.identity.tokens import TokenError, TokenService
from app.modules.identity.types import (
    AccountStatus,
    GrantPurpose,
    OtpChannel,
    OtpPurpose,
)
from app.ports.country import CountryReader
from app.ports.audit import AuditWriter, NoOpAuditWriter
from sqlalchemy.orm import Session


@dataclass(slots=True)
class IdentityApplication:
    """Expose transaction-scoped authentication use cases."""

    registration: RegistrationService
    authentication: AuthenticationService
    recovery: PasswordRecoveryService
    sessions: SessionService
    otp: OtpService
    grants: AuthGrantService
    passwords: PasswordHasher
    accounts: AccountRepository
    tokens: TokenService
    digests: DigestService
    notifications: NotificationSender
    clock: Callable[[], datetime]
    step_up_ttl: timedelta = timedelta(minutes=5)
    identifier_change_ttl: timedelta = timedelta(minutes=5)
    data_export_ttl: timedelta = timedelta(minutes=15)
    events: SecurityEventRecorder | None = None

    def authenticate(
        self,
        token: str,
        correlation_id: UUID,
    ) -> AuthContext:
        """Validate a bearer token against current account state."""
        user_id = self.tokens.read_unverified_subject(token)
        account = self.accounts.get_by_id(user_id)
        if account is None:
            raise TokenError("Access token is invalid.")
        context = self.tokens.validate_access_token(
            token,
            account=account,
            correlation_id=correlation_id,
        )
        self.sessions.assert_active(context.user_id, context.session_id)
        return context

    def request_step_up(
        self,
        *,
        context: AuthContext,
        password: str,
        locale_code: str,
    ) -> UUID:
        """Verify a recent password and send a session-user OTP."""
        account = self.accounts.get_by_id(context.user_id)
        if (
            account is None
            or account.status != AccountStatus.ACTIVE
            or account.password_hash is None
            or account.email is None
            or not self.passwords.verify_password(
                password,
                account.password_hash,
            ).valid
        ):
            raise TokenError("Step-up authentication is unavailable.")
        return self.otp.request(
            user_id=account.user_id,
            destination=account.email,
            channel=OtpChannel.EMAIL,
            purpose=OtpPurpose.STEP_UP,
            locale_code=locale_code,
        )

    def verify_step_up(
        self,
        *,
        context: AuthContext,
        otp_id: UUID,
        code: str,
        action_class: str,
    ) -> str:
        """Exchange an owned step-up OTP for a session-bound grant."""
        challenge = self.otp.verify(otp_id, OtpPurpose.STEP_UP, code)
        if challenge.user_id != context.user_id:
            raise TokenError("Step-up authentication is unavailable.")
        return self.grants.create(
            user_id=context.user_id,
            session_id=context.session_id,
            purpose=GrantPurpose.STEP_UP,
            action_class=action_class,
            subject_hash=None,
            authentication_methods=[*context.authentication_methods, "otp"],
            correlation_id=context.correlation_id,
            ttl=self.step_up_ttl,
        )

    def verify_identifier_change(
        self,
        *,
        context: AuthContext,
        otp_id: UUID,
        code: str,
        purpose: OtpPurpose,
    ) -> str:
        """Exchange an owned candidate OTP for a session-bound change grant.

        Args:
            context: Authenticated principal verifying the candidate.
            otp_id: Opaque candidate verification challenge.
            code: Raw one-time code sent to the candidate.
            purpose: `VERIFY_EMAIL` or `VERIFY_PHONE`.

        Returns:
            Raw single-use grant token, returned once.

        Raises:
            OtpError: If the purpose, owner, channel, or account is invalid.
        """
        binding = binding_for_otp_purpose(purpose)
        if binding is None:
            raise OtpError("OTP challenge is unavailable.")
        challenge = self.otp.verify(
            otp_id,
            binding.otp_purpose,
            code,
            expected_user_id=context.user_id,
        )
        account = self.accounts.get_by_id(context.user_id)
        if (
            challenge.channel != binding.channel
            or account is None
            or account.status != AccountStatus.ACTIVE
        ):
            raise OtpError("OTP challenge is unavailable.")
        return self.grants.create(
            user_id=context.user_id,
            session_id=context.session_id,
            purpose=binding.grant_purpose,
            action_class=binding.action_class,
            subject_hash=challenge.destination_hash,
            authentication_methods=[*context.authentication_methods, "otp"],
            correlation_id=context.correlation_id,
            ttl=self.identifier_change_ttl,
        )

    def verify_data_export(
        self,
        *,
        context: AuthContext,
        otp_id: UUID,
        code: str,
    ) -> str:
        """Exchange an owned export OTP for a session-bound grant."""
        challenge = self.otp.verify(
            otp_id,
            OtpPurpose.DATA_EXPORT,
            code,
            expected_user_id=context.user_id,
        )
        account = self.accounts.get_by_id(context.user_id)
        if (
            challenge.channel != OtpChannel.EMAIL
            or account is None
            or account.status != AccountStatus.ACTIVE
        ):
            raise OtpError("OTP challenge is unavailable.")
        return self.grants.create(
            user_id=context.user_id,
            session_id=context.session_id,
            purpose=GrantPurpose.DATA_EXPORT,
            action_class="DATA_EXPORT",
            subject_hash=None,
            authentication_methods=[*context.authentication_methods, "otp"],
            correlation_id=context.correlation_id,
            ttl=self.data_export_ttl,
        )


class IdentityApplicationBuilder:
    """Build request-scoped identity services with shared secure adapters."""

    def __init__(
        self,
        *,
        digests: DigestService,
        tokens: TokenService,
        countries: CountryReader,
        notifications: NotificationSender,
        rate_limiter: RateLimiter,
        audit_writer: AuditWriter,
        clock: Callable[[], datetime],
        refresh_ttl: timedelta,
        otp_ttl: timedelta = timedelta(minutes=5),
        reset_ttl: timedelta = timedelta(minutes=10),
        otp_max_attempts: int = 5,
        allow_noop_audit: bool = False,
    ) -> None:
        """Initialize reusable adapters and authentication policy."""
        if isinstance(audit_writer, NoOpAuditWriter) and not allow_noop_audit:
            raise ValueError("Authentication requires a transactional audit writer.")
        self.digests = digests
        self.tokens = tokens
        self.countries = countries
        self.notifications = notifications
        self.rate_limiter = rate_limiter
        self.audit_writer = audit_writer
        self.clock = clock
        self.refresh_ttl = refresh_ttl
        self.otp_ttl = otp_ttl
        self.reset_ttl = reset_ttl
        self.otp_max_attempts = otp_max_attempts
        self.passwords = PasswordHasher()
        self.dummy_password_hash = self.passwords.hash_password(
            "dummy-password-never-used"
        )
        self.security_metrics = InMemorySecurityMetrics()
        self.security_sink = LoggingSecurityEventSink()

    def __call__(self, db: Session) -> IdentityApplication:
        """Build identity services sharing one SQLAlchemy transaction."""
        accounts = AccountRepository(db)
        observability = SecurityObservability(
            sink=self.security_sink,
            metrics=self.security_metrics,
            clock=self.clock,
        )
        events = SecurityEventRecorder(
            self.audit_writer,
            DatabaseTransaction(db),
            observability,
        )
        otp = OtpService(
            repository=OtpRepository(db),
            digests=self.digests,
            notifications=self.notifications,
            clock=self.clock,
            ttl=self.otp_ttl,
            max_attempts=self.otp_max_attempts,
            rate_limiter=self.rate_limiter,
            events=events,
        )
        grants = AuthGrantService(
            repository=AuthGrantRepository(db),
            digests=self.digests,
            clock=self.clock,
        )
        sessions = SessionService(
            repository=SessionRepository(db),
            account_repository=accounts,
            digests=self.digests,
            tokens=self.tokens,
            clock=self.clock,
            refresh_ttl=self.refresh_ttl,
            events=events,
            notifications=self.notifications,
        )
        account_service = AccountService(accounts)
        registration = RegistrationService(
            accounts=account_service,
            account_repository=accounts,
            passwords=self.passwords,
            otp=otp,
            countries=self.countries,
            clock=self.clock,
        )
        authentication = AuthenticationService(
            account_repository=accounts,
            passwords=self.passwords,
            sessions=sessions,
            clock=self.clock,
            dummy_password_hash=self.dummy_password_hash,
            countries=self.countries,
            digests=self.digests,
            rate_limiter=self.rate_limiter,
            events=events,
        )
        recovery = PasswordRecoveryService(
            account_repository=accounts,
            passwords=self.passwords,
            otp=otp,
            grants=grants,
            sessions=sessions,
            notifications=self.notifications,
            reset_ttl=self.reset_ttl,
            countries=self.countries,
            events=events,
        )
        return IdentityApplication(
            registration=registration,
            authentication=authentication,
            recovery=recovery,
            sessions=sessions,
            otp=otp,
            grants=grants,
            passwords=self.passwords,
            accounts=accounts,
            tokens=self.tokens,
            digests=self.digests,
            notifications=self.notifications,
            clock=self.clock,
            events=events,
        )
