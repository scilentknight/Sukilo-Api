"""Coordinate customer privacy effects during account closure."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database_context import (
    AuthorizationMode,
    DatabaseRequestContext,
    apply_database_context,
)
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import ConsentRecord
from app.modules.customers.repository import CustomerRepository
from app.modules.customers.types import ConsentSource, ConsentType
from app.modules.identity.privacy import IdentityPrivacyService
from app.ports.audit import AuditEventInput, AuditWriter
from app.ports.country import CountryReader


@dataclass(frozen=True, slots=True)
class RetentionNotice:
    """Describe a country retention notice without customer data."""

    country_id: UUID
    notice: str


class RetentionPolicyReader(Protocol):
    """Read approved closure retention and hold policy."""

    def closure_notice(self, country_id: UUID) -> str:
        """Return the effective country closure notice."""
        ...

    def has_pending_hold(self, user_id: UUID, country_id: UUID) -> bool:
        """Return whether legal or finance policy blocks closure."""
        ...

    def can_pseudonymize_address(self, country_id: UUID) -> bool:
        """Return whether customer address fields are eligible."""
        ...


class DevelopmentRetentionPolicyReader:
    """Provide conservative development-only retention behavior."""

    def closure_notice(self, country_id: UUID) -> str:
        """Return a generic retention notice."""
        del country_id
        return "Required transaction and audit records may be retained."

    def has_pending_hold(self, user_id: UUID, country_id: UUID) -> bool:
        """Report no development hold."""
        del user_id, country_id
        return False

    def can_pseudonymize_address(self, country_id: UUID) -> bool:
        """Allow address pseudonymization in development."""
        del country_id
        return True


class CustomerPrivacyError(Exception):
    """Report a safe customer privacy failure."""


@dataclass(frozen=True, slots=True)
class ClosurePreview:
    """Return closure effects and retention notices."""

    close_grant: str
    effects: tuple[str, ...]
    retention_notices: tuple[RetentionNotice, ...]


class AccountClosureService:
    """Coordinate identity closure and customer pseudonymization atomically."""

    def __init__(
        self,
        *,
        identity: IdentityPrivacyService,
        repository: CustomerRepository,
        retention: RetentionPolicyReader,
        countries: CountryReader,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
        database: Session,
        context_applier: Callable[
            [Session, DatabaseRequestContext],
            None,
        ] = apply_database_context,
    ) -> None:
        """Initialize closure dependencies."""
        self.identity = identity
        self.repository = repository
        self.retention = retention
        self.countries = countries
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock
        self.database = database
        self.context_applier = context_applier

    def request(
        self,
        actor: AuthContext,
        password: str,
        step_up_grant: str,
    ) -> ClosurePreview:
        """Create a confirmation grant without applying closure."""
        grant = self.identity.request_closure(actor, password, step_up_grant)
        notices = tuple(
            RetentionNotice(
                country_id=country_id,
                notice=self.retention.closure_notice(country_id),
            )
            for country_id in self.repository.list_country_ids_by_user(actor.user_id)
        )
        effects = (
            "Authentication and active sessions will be revoked.",
            "Eligible contact and address data will be pseudonymized.",
            "Legally required commerce and audit records will be retained.",
        )
        return ClosurePreview(grant, effects, notices)

    def confirm(self, actor: AuthContext, close_grant: str) -> None:
        """Close identity and pseudonymize eligible customer state."""
        pending = self.identity.begin_closure(actor, close_grant)
        self.context_applier(
            self.database,
            DatabaseRequestContext(
                authorization_mode=AuthorizationMode.ACCOUNT_CLOSURE,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                action_class="ACCOUNT_CLOSURE",
                correlation_id=actor.correlation_id,
            ),
        )
        country_ids = self.repository.list_country_ids_by_user(actor.user_id)
        if any(
            self.retention.has_pending_hold(actor.user_id, country_id)
            for country_id in country_ids
        ):
            raise CustomerPrivacyError("Account closure is blocked.")
        closed = self.identity.finish_closure(actor, pending)
        self._pseudonymize_addresses(actor)
        self._record_evidence(actor, country_ids)
        self.repository.flush()
        self.identity.send_confirmation(closed)

    def _pseudonymize_addresses(self, actor: AuthContext) -> None:
        now = self.clock()
        for address in self.repository.lock_all_addresses_by_user(actor.user_id):
            if not self.retention.can_pseudonymize_address(address.country_id):
                continue
            address.recipient_name = "Closed customer"
            address.phone_e164 = "closed"
            address.address_line_1 = "redacted"
            address.address_line_2 = None
            address.landmark = None
            address.city_text = None
            address.postal_code = None
            address.geography_area_id = None
            address.latitude = None
            address.longitude = None
            address.is_default_shipping = False
            address.is_default_billing = False
            address.deleted_at = address.deleted_at or now
            address.updated_at = now
            address.version += 1

    def _record_evidence(
        self,
        actor: AuthContext,
        country_ids: list[UUID],
    ) -> None:
        now = self.clock()
        for country_id in country_ids:
            policy = self.countries.get_active_policy_version(
                country_id,
                ConsentType.PRIVACY.value,
            )
            self.repository.add_consent(
                ConsentRecord(
                    consent_id=uuid4(),
                    user_id=actor.user_id,
                    country_id=country_id,
                    consent_type=ConsentType.PRIVACY,
                    policy_version=policy,
                    granted=False,
                    source=ConsentSource.SYSTEM,
                    recorded_at=now,
                    correlation_id=actor.correlation_id,
                )
            )
        self.audit_writer.stage(
            AuditEventInput(
                action="iam.customer.pseudonymized",
                resource_type="user_account",
                resource_id=actor.user_id,
                correlation_id=actor.correlation_id,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                after_data={"country_count": len(country_ids)},
            ),
            self.transaction,
        )
