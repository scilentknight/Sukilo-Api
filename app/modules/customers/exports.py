"""Manage verified, asynchronous customer data exports."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.core.auth_context import AuthContext
from app.core.database_context import (
    EXPORT_BY_ID_ACTION,
    JOB_PURPOSE_CUSTOMER_EXPORT,
    AuthorizationMode,
    DatabaseRequestContext,
    apply_database_context,
    bind_database_context,
    validate_database_context,
)
from app.core.security import DigestService, generate_opaque_token
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import DataExportRequest
from app.modules.customers.repository import CustomerRepository
from app.modules.customers.types import DataExportStatus
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.grants import AuthGrantService
from app.modules.identity.otp import OtpService
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.repository import AccountRepository, AuthGrantRepository
from app.modules.identity.types import (
    AccountStatus,
    GrantPurpose,
    OtpChannel,
    OtpPurpose,
)
from app.ports.audit import AuditEventInput, AuditWriter
from app.ports.country import CountryReader

_DATA_EXPORT_TTL = timedelta(minutes=15)
_DOWNLOAD_TTL = timedelta(minutes=5)
_RECENT_AUTH_AGE = timedelta(minutes=15)
_SENSITIVE_EXPORT_KEYS = frozenset(
    {"password", "password_hash", "token", "otp", "address", "email", "phone"}
)


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    """Describe an encrypted stored artifact without exposing a public URL."""

    reference: str
    expires_at: datetime


class ExportArtifactPort(Protocol):
    """Store encrypted exports and issue one-time download references."""

    def store(
        self,
        export_request_id: UUID,
        payload: Mapping[str, object],
    ) -> ExportArtifact:
        """Encrypt and store one generated export."""
        ...

    def create_download_reference(self, artifact_reference: str) -> str:
        """Return one short-lived opaque download reference."""
        ...


class DevelopmentExportArtifactStore:
    """Represent development artifacts with opaque local references."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        """Initialize the development artifact clock."""
        self.clock = clock

    def store(
        self,
        export_request_id: UUID,
        payload: Mapping[str, object],
    ) -> ExportArtifact:
        """Create an opaque artifact reference without a cloud dependency."""
        del payload
        token = generate_opaque_token()
        return ExportArtifact(
            reference=f"development-artifact:{export_request_id}:{token}",
            expires_at=self.clock() + timedelta(hours=1),
        )

    def create_download_reference(self, artifact_reference: str) -> str:
        """Create an opaque one-time reference independent of storage details."""
        del artifact_reference
        return generate_opaque_token()


class CustomerExportError(Exception):
    """Report a safe data-export failure."""


class CustomerExportNotFoundError(CustomerExportError):
    """Hide an absent or foreign export request."""


@dataclass(frozen=True, slots=True)
class ExportStatusView:
    """Expose safe owner-scoped export status."""

    export_request_id: UUID
    status: DataExportStatus
    requested_at: datetime
    completed_at: datetime | None
    downloaded_at: datetime | None


class CustomerExportService:
    """Coordinate verification, generation, and one-time export download."""

    def __init__(
        self,
        *,
        repository: CustomerRepository,
        accounts: AccountRepository,
        grant_repository: AuthGrantRepository,
        grants: AuthGrantService,
        otp: OtpService,
        passwords: PasswordHasher,
        digests: DigestService,
        countries: CountryReader,
        artifacts: ExportArtifactPort,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
        events: SecurityEventRecorder | None = None,
    ) -> None:
        """Initialize data export dependencies."""
        self.repository = repository
        self.accounts = accounts
        self.grant_repository = grant_repository
        self.grants = grants
        self.otp = otp
        self.passwords = passwords
        self.digests = digests
        self.countries = countries
        self.artifacts = artifacts
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock
        self.events = events

    def request_verification(
        self,
        actor: AuthContext,
        password: str,
        locale_code: str,
    ) -> UUID:
        """Verify the current password and send an owned export OTP."""
        account = self.accounts.get_by_id(actor.user_id)
        if (
            account is None
            or account.status != AccountStatus.ACTIVE
            or account.password_hash is None
            or account.email is None
            or account.email_verified_at is None
            or not self.passwords.verify_password(password, account.password_hash).valid
        ):
            raise CustomerExportError("Data export verification is unavailable.")
        return self.otp.request(
            user_id=actor.user_id,
            destination=account.email,
            channel=OtpChannel.EMAIL,
            purpose=OtpPurpose.DATA_EXPORT,
            locale_code=locale_code,
        )

    def verify(
        self,
        actor: AuthContext,
        otp_id: UUID,
        code: str,
    ) -> str:
        """Exchange an owned export OTP for a session-bound grant."""
        challenge = self.otp.verify(
            otp_id,
            OtpPurpose.DATA_EXPORT,
            code,
            expected_user_id=actor.user_id,
        )
        if challenge.channel != OtpChannel.EMAIL:
            raise CustomerExportError("Data export verification is unavailable.")
        return self.grants.create(
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.DATA_EXPORT,
            action_class="DATA_EXPORT",
            subject_hash=None,
            authentication_methods=[*actor.authentication_methods, "otp"],
            correlation_id=actor.correlation_id,
            ttl=_DATA_EXPORT_TTL,
        )

    def request_export(
        self,
        actor: AuthContext,
        country_code: str,
        idempotency_key: str,
        grant: str,
    ) -> DataExportRequest:
        """Consume verification and create one idempotent pending request."""
        country = self.countries.resolve_country(country_code.upper())
        self._bind_self(actor, country.country_id, "DATA_EXPORT")
        existing = self.repository.get_export_by_key(
            actor.user_id,
            country.country_id,
            idempotency_key,
        )
        if existing is not None:
            return existing
        self.grants.consume(
            grant,
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.DATA_EXPORT,
            action_class="DATA_EXPORT",
            subject_hash=None,
        )
        request = DataExportRequest(
            export_request_id=uuid4(),
            user_id=actor.user_id,
            country_id=country.country_id,
            idempotency_key=idempotency_key,
            status=DataExportStatus.PENDING,
            requested_at=self.clock(),
            correlation_id=actor.correlation_id,
        )
        self.repository.add_export(request)
        self.repository.flush()
        self._audit("iam.data_export.requested", request, actor.user_id)
        return request

    def get_status(
        self,
        actor: AuthContext,
        export_request_id: UUID,
    ) -> ExportStatusView:
        """Return owner-scoped status without an artifact reference."""
        self._bind_self(actor, None, EXPORT_BY_ID_ACTION)
        request = self.repository.get_owned_export(
            actor.user_id,
            export_request_id,
        )
        if request is None:
            raise CustomerExportNotFoundError("Export request is unavailable.")
        status = request.status
        if (
            status == DataExportStatus.READY
            and request.artifact_expires_at is not None
            and request.artifact_expires_at <= self.clock()
        ):
            status = DataExportStatus.EXPIRED
        return ExportStatusView(
            export_request_id=request.export_request_id,
            status=status,
            requested_at=request.requested_at,
            completed_at=request.completed_at,
            downloaded_at=request.downloaded_at,
        )

    def create_download_grant(
        self,
        actor: AuthContext,
        export_request_id: UUID,
    ) -> str:
        """Lock a ready export and issue one request-bound download grant."""
        self._require_recent_authentication(actor)
        request = self._lock_owned_export(actor, export_request_id)
        subject_hash = self._subject_hash(export_request_id)
        self.grant_repository.revoke_subject_grants(
            actor.user_id,
            GrantPurpose.DOWNLOAD_EXPORT,
            subject_hash,
            self.clock(),
        )
        return self.grants.create(
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.DOWNLOAD_EXPORT,
            action_class="DOWNLOAD_EXPORT",
            subject_hash=subject_hash,
            authentication_methods=list(actor.authentication_methods),
            correlation_id=actor.correlation_id,
            ttl=_DOWNLOAD_TTL,
        )

    def download(
        self,
        actor: AuthContext,
        export_request_id: UUID,
        grant: str,
    ) -> str:
        """Consume a bound grant and atomically win one download."""
        request = self._lock_owned_export(actor, export_request_id)
        self.grants.consume(
            grant,
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.DOWNLOAD_EXPORT,
            action_class="DOWNLOAD_EXPORT",
            subject_hash=self._subject_hash(export_request_id),
        )
        if request.artifact_reference is None:
            raise CustomerExportError("Export artifact is unavailable.")
        request.status = DataExportStatus.DOWNLOADED
        request.downloaded_at = self.clock()
        request.version += 1
        self.repository.flush()
        self._audit("iam.data_export.downloaded", request, actor.user_id)
        return self.artifacts.create_download_reference(request.artifact_reference)

    def claim(
        self,
        export_request_id: UUID,
        context: DatabaseRequestContext,
    ) -> DataExportRequest:
        """Claim one pending request under a narrowly scoped job context."""
        self._require_job_context(context)
        apply_database_context(self.transaction.session, context)
        request = self.repository.get_export_for_update(export_request_id)
        self._require_matching_job_request(request, context)
        if request is None or request.status != DataExportStatus.PENDING:
            raise CustomerExportError("Export request cannot be claimed.")
        request.status = DataExportStatus.PROCESSING
        request.started_at = self.clock()
        request.version += 1
        self.repository.flush()
        self._audit("iam.data_export.generation_started", request, None)
        return request

    def complete(
        self,
        export_request_id: UUID,
        context: DatabaseRequestContext,
        payload: Mapping[str, object],
    ) -> None:
        """Store and complete one claimed request under explicit job scope."""
        self._require_job_context(context)
        self._reject_sensitive_payload(payload)
        apply_database_context(self.transaction.session, context)
        request = self.repository.get_export_for_update(export_request_id)
        self._require_matching_job_request(request, context)
        if request is None or request.status != DataExportStatus.PROCESSING:
            raise CustomerExportError("Export request cannot be completed.")
        artifact = self.artifacts.store(export_request_id, payload)
        request.artifact_reference = artifact.reference
        request.artifact_expires_at = artifact.expires_at
        request.status = DataExportStatus.READY
        request.completed_at = self.clock()
        request.version += 1
        self.repository.flush()
        self._audit("iam.data_export.generated", request, None)

    def fail(
        self,
        export_request_id: UUID,
        context: DatabaseRequestContext,
        failure_code: str,
    ) -> None:
        """Mark one processing export failed without storing secret details."""
        self._require_job_context(context)
        apply_database_context(self.transaction.session, context)
        request = self.repository.get_export_for_update(export_request_id)
        self._require_matching_job_request(request, context)
        if request is None or request.status != DataExportStatus.PROCESSING:
            raise CustomerExportError("Export request cannot be failed.")
        request.status = DataExportStatus.FAILED
        request.failure_code = failure_code
        request.failed_at = self.clock()
        request.version += 1
        self.repository.flush()
        self._audit("iam.data_export.failed", request, None)

    def _lock_owned_export(
        self,
        actor: AuthContext,
        export_request_id: UUID,
    ) -> DataExportRequest:
        """Read an owned export, then lock it in that export's country."""
        self._bind_self(actor, None, EXPORT_BY_ID_ACTION)
        preview = self.repository.get_owned_export(actor.user_id, export_request_id)
        if preview is None:
            raise CustomerExportNotFoundError("Export request is unavailable.")
        self._bind_self(actor, preview.country_id, "DOWNLOAD_EXPORT")
        return self._owned_ready_for_update(actor.user_id, export_request_id)

    def _bind_self(
        self,
        actor: AuthContext,
        country_id: UUID | None,
        action_class: str,
    ) -> None:
        """Set self-service database context for one export transaction."""
        bind_database_context(
            self.transaction.session,
            DatabaseRequestContext(
                authorization_mode=AuthorizationMode.SELF,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                effective_country_id=country_id,
                action_class=action_class,
                correlation_id=actor.correlation_id,
            ),
        )

    def _owned_ready_for_update(
        self,
        user_id: UUID,
        export_request_id: UUID,
    ) -> DataExportRequest:
        request = self.repository.get_owned_export(
            user_id,
            export_request_id,
            for_update=True,
        )
        if request is None:
            raise CustomerExportNotFoundError("Export request is unavailable.")
        if (
            request.status != DataExportStatus.READY
            or request.artifact_reference is None
            or request.artifact_expires_at is None
            or request.artifact_expires_at <= self.clock()
        ):
            if (
                request.status == DataExportStatus.READY
                and request.artifact_expires_at is not None
                and request.artifact_expires_at <= self.clock()
            ):
                request.status = DataExportStatus.EXPIRED
                request.version += 1
                self._audit("iam.data_export.expired", request, user_id)
            raise CustomerExportError("Export artifact is unavailable.")
        return request

    def _require_recent_authentication(self, actor: AuthContext) -> None:
        if (
            "password" not in actor.authentication_methods
            or self.clock() - actor.authenticated_at > _RECENT_AUTH_AGE
        ):
            raise CustomerExportError("Recent authentication is required.")

    def _subject_hash(self, export_request_id: UUID) -> str:
        return self.digests.digest(
            "data-export-download-subject",
            str(export_request_id),
        )

    def _require_job_context(self, context: DatabaseRequestContext) -> None:
        validate_database_context(context)
        if (
            context.authorization_mode != AuthorizationMode.SYSTEM_JOB
            or context.job_purpose != JOB_PURPOSE_CUSTOMER_EXPORT
        ):
            raise CustomerExportError("Export job context is unavailable.")

    def _require_matching_job_request(
        self,
        request: DataExportRequest | None,
        context: DatabaseRequestContext,
    ) -> None:
        if (
            request is None
            or request.user_id != context.effective_user_id
            or request.country_id != context.effective_country_id
        ):
            raise CustomerExportError("Export job scope is invalid.")

    def _reject_sensitive_payload(self, payload: Mapping[str, object]) -> None:
        if _contains_sensitive_key(payload):
            raise CustomerExportError("Export payload contains restricted data.")

    def _audit(
        self,
        action: str,
        request: DataExportRequest,
        actor_user_id: UUID | None,
    ) -> None:
        self.audit_writer.stage(
            AuditEventInput(
                action=action,
                resource_type="data_export_request",
                resource_id=request.export_request_id,
                correlation_id=request.correlation_id,
                actor_user_id=actor_user_id,
                effective_user_id=request.user_id,
                country_id=request.country_id,
                after_data={"status": request.status.value},
            ),
            self.transaction,
        )
        if self.events is not None:
            self.events.record_signal(
                action,
                request.correlation_id,
                country_id=request.country_id,
                attributes={"reason_code": request.status.value},
            )


def _contains_sensitive_key(value: object) -> bool:
    """Return whether a nested export payload contains a restricted key."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in _SENSITIVE_EXPORT_KEYS):
                return True
            if _contains_sensitive_key(nested):
                return True
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False
