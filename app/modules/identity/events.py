"""Stage PII-safe IAM security events in the active transaction."""

from typing import Mapping
from uuid import UUID, uuid4

from app.core.transactions import DatabaseTransaction
from app.modules.identity.security_events import (
    SecurityEventCode,
    SecurityEventResult,
    SecurityObservability,
)
from app.ports.audit import AuditEventInput, AuditWriter

_OBSERVABILITY_EVENTS: dict[str, tuple[SecurityEventCode, SecurityEventResult]] = {
    "iam.login.succeeded": (
        SecurityEventCode.LOGIN_SUCCEEDED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.login.failed": (
        SecurityEventCode.LOGIN_FAILED,
        SecurityEventResult.FAILURE,
    ),
    "iam.otp.rate_limited": (
        SecurityEventCode.OTP_RATE_LIMITED,
        SecurityEventResult.DENIED,
    ),
    "iam.password.changed": (
        SecurityEventCode.PASSWORD_CHANGED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.session.created": (
        SecurityEventCode.SESSION_CREATED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.session.revoked": (
        SecurityEventCode.SESSION_REVOKED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.session.revoked_all": (
        SecurityEventCode.SESSION_REVOKED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.session.refresh_reuse_detected": (
        SecurityEventCode.SESSION_REFRESH_REUSE_DETECTED,
        SecurityEventResult.DENIED,
    ),
    "iam.assignment.granted": (
        SecurityEventCode.ASSIGNMENT_GRANTED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.assignment.revoked": (
        SecurityEventCode.ASSIGNMENT_REVOKED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.authorization.denied": (
        SecurityEventCode.AUTHORIZATION_DENIED,
        SecurityEventResult.DENIED,
    ),
    "iam.cross_scope.denied": (
        SecurityEventCode.CROSS_SCOPE_DENIED,
        SecurityEventResult.DENIED,
    ),
    "iam.account.closure_requested": (
        SecurityEventCode.ACCOUNT_CLOSURE_REQUESTED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.account.closed": (
        SecurityEventCode.ACCOUNT_CLOSED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.data_export.requested": (
        SecurityEventCode.DATA_EXPORT_REQUESTED,
        SecurityEventResult.SUCCESS,
    ),
    "iam.data_export.downloaded": (
        SecurityEventCode.DATA_EXPORT_DOWNLOADED,
        SecurityEventResult.SUCCESS,
    ),
}
_ATTRIBUTE_NAMES = frozenset(
    {"channel", "permission", "scope_type", "reason_code", "action_class"}
)
_ATTRIBUTE_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
)


class SecurityEventRecorder:
    """Write identity lifecycle evidence through the platform audit port."""

    def __init__(
        self,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        observability: SecurityObservability | None = None,
    ) -> None:
        """Initialize the transaction-bound recorder.

        Args:
            audit_writer: Transactional audit destination.
            transaction: Transaction that owns the staged audit event.
            observability: Optional in-process metrics and event sink.
        """
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.observability = observability

    def record(
        self,
        *,
        action: str,
        resource_type: str,
        resource_id: UUID,
        actor_user_id: UUID | None,
        correlation_id: UUID | None = None,
        after_data: Mapping[str, object] | None = None,
        country_id: UUID | None = None,
    ) -> None:
        """Stage one redacted security event.

        Args:
            action: Stable event action code.
            resource_type: Resource family being changed.
            resource_id: Resource identifier.
            actor_user_id: Authenticated actor, when one exists.
            correlation_id: Request correlation identifier.
            after_data: Redacted attributes safe for audit storage.
            country_id: Country associated with the event, when known.
        """
        resolved_correlation = correlation_id or uuid4()
        self.audit_writer.stage(
            AuditEventInput(
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                actor_user_id=actor_user_id,
                correlation_id=resolved_correlation,
                country_id=country_id,
                after_data=after_data or {},
            ),
            self.transaction,
        )
        self._emit(action, resolved_correlation, country_id, after_data)

    def record_signal(
        self,
        action: str,
        correlation_id: UUID,
        *,
        country_id: UUID | None = None,
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        """Emit one observability event that is not an audit resource change.

        Args:
            action: Stable event action code.
            correlation_id: Request correlation identifier.
            country_id: Country associated with the event, when known.
            attributes: Redacted code attributes.
        """
        self._emit(action, correlation_id, country_id, attributes)

    def _emit(
        self,
        action: str,
        correlation_id: UUID,
        country_id: UUID | None,
        attributes: Mapping[str, object] | None,
    ) -> None:
        """Forward a known event code to the in-process observability port."""
        if self.observability is None:
            return
        mapped = _OBSERVABILITY_EVENTS.get(action)
        if mapped is None:
            return
        event_code, result = mapped
        self.observability.emit(
            event_code,
            result,
            correlation_id,
            country_id=country_id,
            attributes=_safe_attributes(attributes),
        )


def _safe_attributes(
    attributes: Mapping[str, object] | None,
) -> dict[str, str]:
    """Keep only short code attributes accepted by the security event model."""
    if attributes is None:
        return {}
    safe: dict[str, str] = {}
    for name in _ATTRIBUTE_NAMES:
        value = attributes.get(name)
        if (
            isinstance(value, str)
            and 0 < len(value) <= 64
            and set(value) <= _ATTRIBUTE_CHARS
        ):
            safe[name] = value
    return safe
