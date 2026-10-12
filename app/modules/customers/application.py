"""Compose customer services for thin FastAPI routes."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.transactions import DatabaseTransaction
from app.modules.customers.identifier_change import IdentifierChangeService
from app.modules.customers.profile import ProfileService
from app.modules.customers.repository import CustomerRepository
from app.modules.customers.service import (
    AddressService,
    ConsentService,
    PreferenceService,
)
from app.modules.identity.application import IdentityApplicationBuilder
from app.ports.audit import NoOpAuditWriter


@dataclass(frozen=True, slots=True)
class CustomerApplication:
    """Expose request-scoped customer self-service use cases."""

    profile: ProfileService
    identifier_changes: IdentifierChangeService
    preferences: PreferenceService
    consents: ConsentService
    addresses: AddressService


class CustomerApplicationBuilder:
    """Build customer services sharing identity security adapters."""

    def __init__(
        self,
        *,
        identity_builder: IdentityApplicationBuilder,
        allow_noop_audit: bool = False,
    ) -> None:
        """Initialize reusable customer composition settings."""
        if (
            isinstance(identity_builder.audit_writer, NoOpAuditWriter)
            and not allow_noop_audit
        ):
            raise ValueError("Customer mutations require a transactional audit writer.")
        self.identity_builder = identity_builder
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
        return CustomerApplication(
            profile=profile,
            identifier_changes=identifier_changes,
            preferences=preferences,
            consents=consents,
            addresses=addresses,
        )
