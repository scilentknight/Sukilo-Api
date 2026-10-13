"""Test PII-free security events, metrics, and alert rules."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.security import DigestService
from app.modules.identity.security_events import (
    AlertThresholds,
    InMemorySecurityEventSink,
    InMemorySecurityMetrics,
    LoggingSecurityEventSink,
    SecurityAlertCode,
    SecurityEventCode,
    SecurityEventError,
    SecurityEventResult,
    SecurityMetric,
    SecurityObservability,
    SecurityWindow,
    evaluate_alert_rules,
    hash_signal,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
CORRELATION_ID = UUID(int=9)
COUNTRY_ID = UUID(int=4)
DIGESTS = DigestService(active_key_id="v1", peppers={"v1": b"p" * 32})
EXPECTED_CODES = {
    "iam.login.succeeded",
    "iam.login.failed",
    "iam.otp.rate_limited",
    "iam.password.changed",
    "iam.session.created",
    "iam.session.revoked",
    "iam.session.refresh_reuse_detected",
    "iam.assignment.granted",
    "iam.assignment.revoked",
    "iam.authorization.denied",
    "iam.cross_scope.denied",
    "iam.account.closure_requested",
    "iam.account.closed",
    "iam.data_export.requested",
    "iam.data_export.downloaded",
}


def _observability() -> tuple[
    SecurityObservability,
    InMemorySecurityEventSink,
    InMemorySecurityMetrics,
]:
    sink = InMemorySecurityEventSink()
    metrics = InMemorySecurityMetrics()
    return SecurityObservability(sink=sink, metrics=metrics, clock=lambda: NOW), sink, metrics


def test_event_codes_are_exactly_the_phase_five_list() -> None:
    """Expose only the documented event codes."""
    assert {code.value for code in SecurityEventCode} == EXPECTED_CODES


def test_event_has_required_fields_and_hashed_signals_only() -> None:
    """Carry timestamp, correlation ID, code, result, country, and hashes."""
    observability, sink, _ = _observability()
    ip_hash = hash_signal(DIGESTS, "ip_hash", "203.0.113.9")
    event = observability.emit(
        SecurityEventCode.LOGIN_FAILED,
        SecurityEventResult.FAILURE,
        CORRELATION_ID,
        country_id=COUNTRY_ID,
        signals={"ip_hash": ip_hash},
        attributes={"channel": "EMAIL"},
    )
    payload = event.as_dict()
    assert sink.events == [event]
    assert payload["timestamp"] == NOW.isoformat()
    assert payload["correlation_id"] == str(CORRELATION_ID)
    assert payload["event_code"] == "iam.login.failed"
    assert payload["result"] == "FAILURE"
    assert payload["country_id"] == str(COUNTRY_ID)
    assert "203.0.113.9" not in json.dumps(payload)


def test_country_is_optional() -> None:
    """Allow events whose country is not yet known."""
    observability, _, _ = _observability()
    event = observability.emit(
        SecurityEventCode.PASSWORD_CHANGED,
        SecurityEventResult.SUCCESS,
        CORRELATION_ID,
    )
    assert event.country_id is None


@pytest.mark.parametrize(
    "bad_signals",
    [
        {"ip_hash": "203.0.113.9"},
        {"destination_hash": "buyer@example.com"},
        {"email": "buyer@example.com"},
        {"phone": "+9779841234567"},
        {"address": "12 Lake Road"},
        {"password": "hunter2"},
        {"token": "t" * 43},
        {"otp": "123456"},
        {"request_body": "{}"},
        {"ip_hash": {"nested": "x"}},
        {"unknown_hash": "v1$hmac-sha256$" + "A" * 43},
    ],
)
def test_sensitive_or_unhashed_signals_are_rejected(bad_signals: dict[str, object]) -> None:
    """Reject raw sensitive names, raw values, and non-digest signals."""
    observability, sink, _ = _observability()
    with pytest.raises(SecurityEventError):
        observability.emit(
            SecurityEventCode.LOGIN_FAILED,
            SecurityEventResult.FAILURE,
            CORRELATION_ID,
            signals=bad_signals,
        )
    assert sink.events == []


@pytest.mark.parametrize(
    "bad_attributes",
    [
        {"email": "x"},
        {"channel": "buyer@example.com"},
        {"channel": "+9779841234567"},
        {"channel": "123456"},
        {"channel": "12 Lake Road"},
        {"channel": "a" * 43},
        {"body": "x"},
        {"surprise": "EMAIL"},
    ],
)
def test_sensitive_attributes_are_rejected(bad_attributes: dict[str, object]) -> None:
    """Reject attributes that could hold identifiers, OTPs, or tokens."""
    observability, sink, _ = _observability()
    with pytest.raises(SecurityEventError):
        observability.emit(
            SecurityEventCode.LOGIN_FAILED,
            SecurityEventResult.FAILURE,
            CORRELATION_ID,
            attributes=bad_attributes,
        )
    assert sink.events == []


def test_hash_signal_rejects_unknown_signal_name() -> None:
    """Allow hashing only for approved signal names."""
    with pytest.raises(SecurityEventError):
        hash_signal(DIGESTS, "email", "buyer@example.com")


def test_events_update_the_listed_metrics() -> None:
    """Increment login, rate-limit, replay, revocation, and denial metrics."""
    observability, _, metrics = _observability()
    send = observability.emit
    send(SecurityEventCode.LOGIN_FAILED, SecurityEventResult.FAILURE, CORRELATION_ID,
         country_id=COUNTRY_ID, attributes={"channel": "EMAIL"})
    send(SecurityEventCode.LOGIN_SUCCEEDED, SecurityEventResult.SUCCESS, CORRELATION_ID,
         country_id=COUNTRY_ID, attributes={"channel": "EMAIL"})
    send(SecurityEventCode.OTP_RATE_LIMITED, SecurityEventResult.DENIED, CORRELATION_ID,
         country_id=COUNTRY_ID)
    send(SecurityEventCode.SESSION_REFRESH_REUSE_DETECTED, SecurityEventResult.DENIED,
         CORRELATION_ID, country_id=COUNTRY_ID)
    send(SecurityEventCode.SESSION_REVOKED, SecurityEventResult.SUCCESS, CORRELATION_ID)
    send(SecurityEventCode.CROSS_SCOPE_DENIED, SecurityEventResult.DENIED, CORRELATION_ID,
         country_id=COUNTRY_ID,
         attributes={"permission": "iam.assignment.manage", "scope_type": "COUNTRY"})
    observability.record_otp_outcome(COUNTRY_ID, "EMAIL", "VERIFIED")
    observability.record_rls_denial(COUNTRY_ID)
    observability.record_job_outcome("RETENTION", "SUCCESS", COUNTRY_ID)
    observability.record_audit_lag(12.5)
    assert metrics.total(SecurityMetric.LOGIN_OUTCOMES, result="FAILURE") == 1
    assert metrics.total(SecurityMetric.LOGIN_OUTCOMES, channel="EMAIL") == 2
    assert metrics.total(SecurityMetric.RATE_LIMIT_ACTIVATIONS) == 1
    assert metrics.total(SecurityMetric.REFRESH_REPLAY_DETECTIONS) == 1
    assert metrics.total(SecurityMetric.SESSION_REVOCATIONS) == 1
    assert metrics.total(SecurityMetric.AUTHORIZATION_DENIALS, scope_type="COUNTRY") == 1
    assert metrics.total(SecurityMetric.CROSS_COUNTRY_DENIALS) == 1
    assert metrics.total(SecurityMetric.OTP_OUTCOMES) == 1
    assert metrics.total(SecurityMetric.RLS_DENIALS) == 1
    assert metrics.total(SecurityMetric.JOB_OUTCOMES, action_class="RETENTION") == 1
    assert metrics.gauges[(SecurityMetric.AUDIT_OUTBOX_LAG_SECONDS, ())] == 12.5


def test_logging_sink_writes_json_without_network(caplog: pytest.LogCaptureFixture) -> None:
    """Log one JSON line per event through the local logger."""
    sink = LoggingSecurityEventSink()
    observability = SecurityObservability(
        sink=sink, metrics=InMemorySecurityMetrics(), clock=lambda: NOW
    )
    with caplog.at_level("INFO", logger="sukilo.security_events"):
        observability.emit(
            SecurityEventCode.ACCOUNT_CLOSED, SecurityEventResult.SUCCESS, CORRELATION_ID
        )
    assert json.loads(caplog.records[0].getMessage())["event_code"] == "iam.account.closed"


def _codes(window: SecurityWindow) -> set[SecurityAlertCode]:
    return {alert.code for alert in evaluate_alert_rules(window)}


def test_healthy_window_fires_no_alerts() -> None:
    """Stay quiet for a baseline window."""
    assert evaluate_alert_rules(SecurityWindow()) == ()


def test_each_alert_rule_fires_on_its_condition() -> None:
    """Fire every documented alert rule from its own window."""
    cases = {
        SecurityAlertCode.CREDENTIAL_STUFFING: SecurityWindow(
            login_failures=60, distinct_failed_identifiers=30),
        SecurityAlertCode.OTP_SPIKE: SecurityWindow(otp_requests=150, otp_verifications=60),
        SecurityAlertCode.REFRESH_REPLAY: SecurityWindow(refresh_replays=2),
        SecurityAlertCode.CROSS_COUNTRY_TAMPERING: SecurityWindow(cross_country_denials=3),
        SecurityAlertCode.RLS_CONTEXT_MISSING: SecurityWindow(rls_context_missing=1),
        SecurityAlertCode.AUDIT_LAG: SecurityWindow(audit_lag_seconds=301),
        SecurityAlertCode.ROTATION_OR_RETENTION_FAILURE: SecurityWindow(retention_failures=1),
        SecurityAlertCode.UNEXPECTED_RUNTIME_PRIVILEGES: SecurityWindow(
            unexpected_runtime_privileges=frozenset({"BYPASSRLS"})),
    }
    for code, window in cases.items():
        assert code in _codes(window), code
    assert SecurityAlertCode.ROTATION_OR_RETENTION_FAILURE in _codes(
        SecurityWindow(rotation_failures=1)
    )


def test_alert_rules_respect_baselines_and_thresholds() -> None:
    """Avoid alerts at the baseline and honor custom thresholds."""
    assert _codes(SecurityWindow(login_failures=60, distinct_failed_identifiers=2)) == set()
    assert _codes(SecurityWindow(refresh_replays=2, refresh_replay_baseline=2)) == set()
    assert _codes(SecurityWindow(cross_country_denials=2)) == set()
    strict = AlertThresholds(cross_country_denials=1)
    alerts = evaluate_alert_rules(SecurityWindow(cross_country_denials=1), strict)
    assert [alert.code for alert in alerts] == [SecurityAlertCode.CROSS_COUNTRY_TAMPERING]


def test_alert_payload_has_no_identifiers() -> None:
    """Limit alerts to codes, severity, and numbers."""
    alerts = evaluate_alert_rules(SecurityWindow(rls_context_missing=2))
    assert repr(alerts[0]).count("UUID") == 0
    assert set(alerts[0].__slots__) == {"code", "severity", "observed", "threshold"}
