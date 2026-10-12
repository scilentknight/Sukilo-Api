"""Stage PII-safe IAM security events in the active transaction."""

from typing import Mapping
from uuid import UUID, uuid4

from app.core.transactions import DatabaseTransaction
from app.ports.audit import AuditEventInput, AuditWriter


class SecurityEventRecorder:
    """Write identity lifecycle evidence through the platform audit port."""

    def __init__(
        self,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
    ) -> None:
        """Initialize the transaction-bound recorder."""
        self.audit_writer = audit_writer
        self.transaction = transaction

    def record(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: UUID,
        actor_user_id: UUID | None,
        correlation_id: UUID | None = None,
        after_data: Mapping[str, object] | None = None,
    ) -> None:
        """Stage one redacted security event."""
        self.audit_writer.stage(
            AuditEventInput(
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                actor_user_id=actor_user_id,
                correlation_id=correlation_id or uuid4(),
                after_data=after_data or {},
            ),
            self.transaction,
        )
