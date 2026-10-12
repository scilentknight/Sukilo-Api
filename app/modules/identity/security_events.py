"""Emit PII-free IAM security events, in-process metrics, and alert rules.

Nothing here performs network I/O. Sinks and metric ports are in-process
adapters; production exporters can implement the same protocols later.
"""

import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.security import DigestService

_LOGGER = logging.getLogger("sukilo.security_events")
_ALLOWED_SIGNALS = frozenset(
    {
        "ip_hash",
        "user_agent_hash",
        "destination_hash",
        "identifier_hash",
        "session_hash",
        "device_hash",
    }
)
_ALLOWED_ATTRIBUTES = frozenset(
    {"channel", "permission", "scope_type", "reason_code", "action_class"}
)
_FORBIDDEN_KEY_PARTS = (
    "email",
    "phone",
    "address",
    "password",
    "token",
    "otp",
    "body",
    "request",
    "secret",
    "authorization",
    "cookie",
    "credential",
)
_SIGNAL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}\$hmac-sha256\$[A-Za-z0-9_-]{43}$")
_ATTRIBUTE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_ALL_DIGITS = re.compile(r"\d+")
_LONG_DIGITS = re.compile(r"\d{7,}")
_OPAQUE_LEAK = re.compile(r"[A-Za-z0-9_-]{32,}")


class SecurityEventError(ValueError):
    """Reject an event that could carry sensitive data."""


class SecurityEventCode(StrEnum):
    """List the exact IAM security event codes."""

    LOGIN_SUCCEEDED = "iam.login.succeeded"
    LOGIN_FAILED = "iam.login.failed"
    OTP_RATE_LIMITED = "iam.otp.rate_limited"
    PASSWORD_CHANGED = "iam.password.changed"
    SESSION_CREATED = "iam.session.created"
    SESSION_REVOKED = "iam.session.revoked"
    SESSION_REFRESH_REUSE_DETECTED = "iam.session.refresh_reuse_detected"
    ASSIGNMENT_GRANTED = "iam.assignment.granted"
    ASSIGNMENT_REVOKED = "iam.assignment.revoked"
    AUTHORIZATION_DENIED = "iam.authorization.denied"
    CROSS_SCOPE_DENIED = "iam.cross_scope.denied"
    ACCOUNT_CLOSURE_REQUESTED = "iam.account.closure_requested"
    ACCOUNT_CLOSED = "iam.account.closed"
    DATA_EXPORT_REQUESTED = "iam.data_export.requested"
    DATA_EXPORT_DOWNLOADED = "iam.data_export.downloaded"


class SecurityEventResult(StrEnum):
    """Describe the outcome of the evented operation."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    DENIED = "DENIED"


class SecurityMetric(StrEnum):
    """Name the IAM security metrics."""

    LOGIN_OUTCOMES = "iam_login_outcomes_total"
    OTP_OUTCOMES = "iam_otp_outcomes_total"
    RATE_LIMIT_ACTIVATIONS = "iam_rate_limit_activations_total"
    REFRESH_REPLAY_DETECTIONS = "iam_refresh_replay_detections_total"
    SESSION_REVOCATIONS = "iam_session_revocations_total"
    AUTHORIZATION_DENIALS = "iam_authorization_denials_total"
    CROSS_COUNTRY_DENIALS = "iam_cross_country_denials_total"
    RLS_DENIALS = "iam_rls_denials_total"
    JOB_OUTCOMES = "iam_job_outcomes_total"
    AUDIT_OUTBOX_LAG_SECONDS = "iam_audit_outbox_lag_seconds"


@dataclass(frozen=True, slots=True)
class SecurityEvent:
    """Carry one validated, PII-free structured security event."""

    timestamp: datetime
    correlation_id: UUID
    event_code: SecurityEventCode
    result: SecurityEventResult
    country_id: UUID | None
    signals: Mapping[str, str]
    attributes: Mapping[str, str]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe mapping of the event."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": str(self.correlation_id),
            "event_code": self.event_code.value,
            "result": self.result.value,
            "country_id": None if self.country_id is None else str(self.country_id),
            "signals": dict(self.signals),
            "attributes": dict(self.attributes),
        }


class SecurityEventSink(Protocol):
    """Receive validated events."""

    def emit(self, event: SecurityEvent) -> None:
        """Accept one event."""
        ...


class SecurityMetrics(Protocol):
    """Count security metrics in process."""

    def increment(
        self,
        metric: SecurityMetric,
        labels: Mapping[str, str],
        value: int = 1,
    ) -> None:
        """Add to a counter."""
        ...

    def set_gauge(
        self,
        metric: SecurityMetric,
        labels: Mapping[str, str],
        value: float,
    ) -> None:
        """Set a gauge value."""
        ...


class InMemorySecurityEventSink:
    """Keep emitted events in memory."""

    def __init__(self) -> None:
        """Initialize an empty event list."""
        self.events: list[SecurityEvent] = []

    def emit(self, event: SecurityEvent) -> None:
        """Store one event."""
        self.events.append(event)


class LoggingSecurityEventSink:
    """Write each event as one JSON log line to the local logger."""

    def emit(self, event: SecurityEvent) -> None:
        """Log one event."""
        _LOGGER.info(json.dumps(event.as_dict(), sort_keys=True))


class InMemorySecurityMetrics:
    """Store counters and gauges keyed by metric and labels."""

    def __init__(self) -> None:
        """Initialize empty counters and gauges."""
        self.counters: Counter[tuple[SecurityMetric, tuple[tuple[str, str], ...]]] = Counter()
        self.gauges: dict[tuple[SecurityMetric, tuple[tuple[str, str], ...]], float] = {}

    def increment(
        self,
        metric: SecurityMetric,
        labels: Mapping[str, str],
        value: int = 1,
    ) -> None:
        """Add to one labelled counter."""
        self.counters[(metric, _label_key(labels))] += value

    def set_gauge(
        self,
        metric: SecurityMetric,
        labels: Mapping[str, str],
        value: float,
    ) -> None:
        """Set one labelled gauge."""
        self.gauges[(metric, _label_key(labels))] = value

    def total(self, metric: SecurityMetric, **labels: str) -> int:
        """Sum counters whose labels include every given label."""
        wanted = set(labels.items())
        return sum(
            count
            for (name, key), count in self.counters.items()
            if name is metric and wanted <= set(key)
        )


def _label_key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Return a stable hashable label key."""
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def hash_signal(digests: DigestService, name: str, raw_value: str) -> str:
    """Hash one raw security signal with the keyed digest service.

    Args:
        digests: Versioned keyed digest service.
        name: Allowed signal name such as ``ip_hash``.
        raw_value: Raw value to hash; it is never stored.

    Returns:
        Self-describing keyed digest.

    Raises:
        SecurityEventError: If the signal name is not allowed.
    """
    if name not in _ALLOWED_SIGNALS:
        raise SecurityEventError("Security signal name is not allowed.")
    return digests.digest(f"security-signal:{name}", raw_value)


def _reject_key(name: object) -> None:
    """Reject a payload key that names sensitive data."""
    lowered = str(name).casefold()
    if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
        raise SecurityEventError("Security event payload contains a forbidden field.")


def _validate_signals(signals: Mapping[str, object]) -> dict[str, str]:
    """Return signals after requiring allowed names and digest values."""
    clean: dict[str, str] = {}
    for name, value in signals.items():
        _reject_key(name)
        if name not in _ALLOWED_SIGNALS:
            raise SecurityEventError("Security signal name is not allowed.")
        if not isinstance(value, str) or _SIGNAL_PATTERN.fullmatch(value) is None:
            raise SecurityEventError("Security signals must be keyed digests.")
        clean[name] = value
    return clean


def _validate_attributes(attributes: Mapping[str, object]) -> dict[str, str]:
    """Return attributes after allowlisting names and screening values."""
    clean: dict[str, str] = {}
    for name, value in attributes.items():
        _reject_key(name)
        if name not in _ALLOWED_ATTRIBUTES:
            raise SecurityEventError("Security attribute name is not allowed.")
        if (
            not isinstance(value, str)
            or _ATTRIBUTE_PATTERN.fullmatch(value) is None
            or _ALL_DIGITS.fullmatch(value) is not None
            or _LONG_DIGITS.search(value) is not None
            or _OPAQUE_LEAK.search(value) is not None
        ):
            raise SecurityEventError("Security attribute value is not allowed.")
        clean[name] = value
    return clean


class SecurityObservability:
    """Validate events, forward them to a sink, and update metrics."""

    def __init__(
        self,
        *,
        sink: SecurityEventSink,
        metrics: SecurityMetrics,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize the sink, metrics port, and clock."""
        self.sink = sink
        self.metrics = metrics
        self.clock = clock

    def emit(
        self,
        event_code: SecurityEventCode,
        result: SecurityEventResult,
        correlation_id: UUID,
        *,
        country_id: UUID | None = None,
        signals: Mapping[str, object] | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> SecurityEvent:
        """Validate and emit one security event.

        Args:
            event_code: One of the exact IAM event codes.
            result: Operation outcome.
            correlation_id: Request or job correlation ID.
            country_id: Country when known.
            signals: Hashed signals keyed by allowed name.
            attributes: Safe code-like attributes such as channel.

        Returns:
            The emitted event.

        Raises:
            SecurityEventError: If the payload could carry sensitive data.
        """
        event = SecurityEvent(
            timestamp=self.clock(),
            correlation_id=correlation_id,
            event_code=SecurityEventCode(event_code),
            result=SecurityEventResult(result),
            country_id=country_id,
            signals=_validate_signals(signals or {}),
            attributes=_validate_attributes(attributes or {}),
        )
        self.sink.emit(event)
        self._count(event)
        return event

    def record_otp_outcome(self, country_id: UUID | None, channel: str, result: str) -> None:
        """Count one OTP request or verification outcome."""
        labels = _validate_attributes({"channel": channel, "reason_code": result})
        self.metrics.increment(
            SecurityMetric.OTP_OUTCOMES,
            {"country": _country(country_id), **labels},
        )

    def record_rls_denial(self, country_id: UUID | None) -> None:
        """Count one row-level-security denial."""
        self.metrics.increment(
            SecurityMetric.RLS_DENIALS,
            {"country": _country(country_id)},
        )

    def record_job_outcome(self, job: str, result: str, country_id: UUID | None) -> None:
        """Count one closure, export, retention, or rotation job outcome."""
        labels = _validate_attributes({"action_class": job, "reason_code": result})
        self.metrics.increment(
            SecurityMetric.JOB_OUTCOMES,
            {"country": _country(country_id), **labels},
        )

    def record_audit_lag(self, seconds: float) -> None:
        """Set the current audit and outbox delivery lag."""
        self.metrics.set_gauge(SecurityMetric.AUDIT_OUTBOX_LAG_SECONDS, {}, seconds)

    def _count(self, event: SecurityEvent) -> None:
        """Update counters implied by one event."""
        country = _country(event.country_id)
        attrs = event.attributes
        code = event.event_code
        if code in (SecurityEventCode.LOGIN_SUCCEEDED, SecurityEventCode.LOGIN_FAILED):
            self.metrics.increment(
                SecurityMetric.LOGIN_OUTCOMES,
                {"country": country, "channel": attrs.get("channel", "unknown"),
                 "result": event.result.value},
            )
        elif code is SecurityEventCode.OTP_RATE_LIMITED:
            self.metrics.increment(SecurityMetric.RATE_LIMIT_ACTIVATIONS, {"country": country})
        elif code is SecurityEventCode.SESSION_REFRESH_REUSE_DETECTED:
            self.metrics.increment(SecurityMetric.REFRESH_REPLAY_DETECTIONS, {"country": country})
        elif code is SecurityEventCode.SESSION_REVOKED:
            self.metrics.increment(SecurityMetric.SESSION_REVOCATIONS, {"country": country})
        elif code in (SecurityEventCode.AUTHORIZATION_DENIED, SecurityEventCode.CROSS_SCOPE_DENIED):
            self._count_denial(event, country)

    def _count_denial(self, event: SecurityEvent, country: str) -> None:
        """Count authorization and cross-country denials."""
        self.metrics.increment(
            SecurityMetric.AUTHORIZATION_DENIALS,
            {
                "permission": event.attributes.get("permission", "unknown"),
                "scope_type": event.attributes.get("scope_type", "unknown"),
            },
        )
        if event.event_code is SecurityEventCode.CROSS_SCOPE_DENIED:
            self.metrics.increment(SecurityMetric.CROSS_COUNTRY_DENIALS, {"country": country})


def _country(country_id: UUID | None) -> str:
    """Return a metric label for an optional country."""
    return "unknown" if country_id is None else str(country_id)


class SecurityAlertCode(StrEnum):
    """Name the alert rules."""

    CREDENTIAL_STUFFING = "credential_stuffing"
    OTP_SPIKE = "otp_spike"
    REFRESH_REPLAY = "refresh_replay"
    CROSS_COUNTRY_TAMPERING = "cross_country_tampering"
    RLS_CONTEXT_MISSING = "rls_context_missing"
    AUDIT_LAG = "audit_lag"
    ROTATION_OR_RETENTION_FAILURE = "rotation_or_retention_failure"
    UNEXPECTED_RUNTIME_PRIVILEGES = "unexpected_runtime_privileges"


@dataclass(frozen=True, slots=True)
class SecurityWindow:
    """Summarize observed counts for one evaluation window."""

    login_failures: int = 0
    distinct_failed_identifiers: int = 0
    otp_requests: int = 0
    otp_verifications: int = 0
    refresh_replays: int = 0
    refresh_replay_baseline: int = 0
    cross_country_denials: int = 0
    rls_context_missing: int = 0
    audit_lag_seconds: float = 0.0
    rotation_failures: int = 0
    retention_failures: int = 0
    unexpected_runtime_privileges: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    """Hold tunable alert thresholds."""

    stuffing_failures: int = 50
    stuffing_distinct_identifiers: int = 20
    otp_total: int = 200
    cross_country_denials: int = 3
    audit_lag_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class SecurityAlert:
    """Describe a fired alert without identifiers or payload data."""

    code: SecurityAlertCode
    severity: str
    observed: float
    threshold: float


DEFAULT_ALERT_THRESHOLDS = AlertThresholds()


def evaluate_alert_rules(
    window: SecurityWindow,
    thresholds: AlertThresholds = DEFAULT_ALERT_THRESHOLDS,
) -> tuple[SecurityAlert, ...]:
    """Return alerts fired by one window of counts.

    Args:
        window: Observed counts for the evaluation window.
        thresholds: Alert thresholds.

    Returns:
        Fired alerts in deterministic order; empty when healthy.
    """
    candidates = (
        (
            SecurityAlertCode.CREDENTIAL_STUFFING, "high",
            window.login_failures, thresholds.stuffing_failures,
            window.login_failures >= thresholds.stuffing_failures
            and window.distinct_failed_identifiers >= thresholds.stuffing_distinct_identifiers,
        ),
        (
            SecurityAlertCode.OTP_SPIKE, "medium",
            window.otp_requests + window.otp_verifications, thresholds.otp_total,
            window.otp_requests + window.otp_verifications >= thresholds.otp_total,
        ),
        (
            SecurityAlertCode.REFRESH_REPLAY, "high",
            window.refresh_replays, window.refresh_replay_baseline,
            window.refresh_replays > window.refresh_replay_baseline,
        ),
        (
            SecurityAlertCode.CROSS_COUNTRY_TAMPERING, "high",
            window.cross_country_denials, thresholds.cross_country_denials,
            window.cross_country_denials >= thresholds.cross_country_denials,
        ),
        (
            SecurityAlertCode.RLS_CONTEXT_MISSING, "critical",
            window.rls_context_missing, 1, window.rls_context_missing >= 1,
        ),
        (
            SecurityAlertCode.AUDIT_LAG, "high",
            window.audit_lag_seconds, thresholds.audit_lag_seconds,
            window.audit_lag_seconds > thresholds.audit_lag_seconds,
        ),
        (
            SecurityAlertCode.ROTATION_OR_RETENTION_FAILURE, "high",
            window.rotation_failures + window.retention_failures, 1,
            window.rotation_failures + window.retention_failures >= 1,
        ),
        (
            SecurityAlertCode.UNEXPECTED_RUNTIME_PRIVILEGES, "critical",
            len(window.unexpected_runtime_privileges), 1,
            bool(window.unexpected_runtime_privileges),
        ),
    )
    return tuple(
        SecurityAlert(code, severity, observed, threshold)
        for code, severity, observed, threshold, fired in candidates
        if fired
    )
