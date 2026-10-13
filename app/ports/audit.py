"""Define transaction-bound privileged audit recording."""

from dataclasses import dataclass, field
from typing import Mapping, Protocol
from uuid import UUID

from app.core.transactions import DatabaseTransaction


@dataclass(frozen=True, slots=True)
class AuditEventInput:
    """Describe a redacted security or privileged audit event."""

    action: str
    resource_type: str
    resource_id: UUID
    correlation_id: UUID
    actor_user_id: UUID | None = None
    effective_user_id: UUID | None = None
    country_id: UUID | None = None
    vendor_id: UUID | None = None
    before_data: Mapping[str, object] = field(default_factory=dict)
    after_data: Mapping[str, object] = field(default_factory=dict)
    ip_hash: str | None = None
    user_agent_hash: str | None = None


class AuditWriter(Protocol):
    """Stage audit evidence in an existing database transaction."""

    def stage(
        self,
        event: AuditEventInput,
        transaction: DatabaseTransaction,
    ) -> None:
        """Stage one audit event without committing."""
        ...


class NoOpAuditWriter:
    """Discard audit events only in non-production development."""

    def stage(
        self,
        event: AuditEventInput,
        transaction: DatabaseTransaction,
    ) -> None:
        """Discard a development audit event.

        Args:
            event: Redacted audit event.
            transaction: Active database transaction.
        """
        del event, transaction
