"""Compose customer services for thin FastAPI routes."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.transactions import DatabaseTransaction
from app.modules.customers.identifier_change import IdentifierChangeService
from app.modules.customers.exports import (
    CustomerExportService,
    DevelopmentExportArtifactStore,
    ExportArtifactPort,
)
from app.modules.customers.privacy import (
    AccountClosureService,
    DevelopmentRetentionPolicyReader,
    RetentionPolicyReader,
)
from app.modules.customers.profile import ProfileService
from app.modules.customers.repository import CustomerRepository
from app.modules.customers.service import (
    AddressService,
    ConsentService,
    PreferenceService,
)
from app.modules.identity.application import IdentityApplicationBuilder
from app.modules.identity.privacy import IdentityPrivacyService
from app.modules.identity.repository import (
    AuthGrantRepository,
    OtpRepository,
    SessionRepository,
)
from app.ports.audit import NoOpAuditWriter


@dataclass(frozen=True, slots=True)
class CustomerApplication:
    """Expose request-scoped customer self-service use cases."""

    profile: ProfileService
    identifier_changes: IdentifierChangeService
    preferences: PreferenceService
    consents: ConsentService
    addresses: AddressService
    closure: AccountClosureService
    exports: CustomerExportService


class CustomerApplicationBuilder:
    """Build customer services sharing identity security adapters."""

    def __init__(
        self,
        *,
        identity_builder: IdentityApplicationBuilder,
        retention: RetentionPolicyReader | None = None,
        artifacts: ExportArtifactPort | None = None,
        allow_noop_audit: bool = False,
    ) -> None:
        """Initialize reusable customer composition settings."""
        if (
            isinstance(identity_builder.audit_writer, NoOpAuditWriter)
            and not allow_noop_audit
        ):
            raise ValueError("Customer mutations require a transactional audit writer.")
        self.identity_builder = identity_builder
        self.retention = retention or DevelopmentRetentionPolicyReader()
        self.artifacts = artifacts
        self.allow_noop_audit = allow_noop_audit

    def __call__(self, db: Session) -> CustomerApplication:
        """Build customer services for one database transaction."""
        identity = self.identity_builder(db)
        repository = CustomerRepository(db)
        transaction = DatabaseTransaction(db)
        clock = self.identity_builder.clock
        preferences = PreferenceService(
            repository=repository,
            countries=self.identity_builder.countries,
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
        )
        consents = ConsentService(
            repository=repository,
            countries=self.identity_builder.countries,
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
        )
        addresses = AddressService(
            repository=repository,
            countries=self.identity_builder.countries,
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
        )
        profile = ProfileService(
            accounts=identity.accounts,
            preferences=preferences,
            sessions=identity.sessions,
        )
        identifier_changes = IdentifierChangeService(
            accounts=identity.accounts,
            otp=identity.otp,
            grants=identity.grants,
            digests=identity.digests,
            sessions=identity.sessions,
            notifications=identity.notifications,
            countries=self.identity_builder.countries,
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
        )
        grant_repository = AuthGrantRepository(db)
        identity_privacy = IdentityPrivacyService(
            accounts=identity.accounts,
            grant_repository=grant_repository,
            otp_repository=OtpRepository(db),
            session_repository=SessionRepository(db),
            grants=identity.grants,
            passwords=identity.passwords,
            digests=identity.digests,
            notifications=identity.notifications,
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
            events=identity.events,
        )
        closure = AccountClosureService(
            identity=identity_privacy,
            repository=repository,
            retention=self.retention,
            countries=self.identity_builder.countries,
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
            database=db,
        )
        exports = CustomerExportService(
            repository=repository,
            accounts=identity.accounts,
            grant_repository=grant_repository,
            grants=identity.grants,
            otp=identity.otp,
            passwords=identity.passwords,
            digests=identity.digests,
            countries=self.identity_builder.countries,
            artifacts=self.artifacts or DevelopmentExportArtifactStore(clock),
            audit_writer=self.identity_builder.audit_writer,
            transaction=transaction,
            clock=clock,
            events=identity.events,
        )
        return CustomerApplication(
            profile=profile,
            identifier_changes=identifier_changes,
            preferences=preferences,
            consents=consents,
            addresses=addresses,
            closure=closure,
            exports=exports,
        )
