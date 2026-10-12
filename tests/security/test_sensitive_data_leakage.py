"""Scan representative audit, export, security-event, and log output for PII."""

import dataclasses
import json
import re
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.database_context import AuthorizationMode, DatabaseRequestContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.customers.exports import CustomerExportError
from app.modules.customers.models import Address
from app.modules.customers.retention import (
    DATA_CLASS_CUSTOMER_ADDRESS,
    InMemoryRetentionObserver,
    RetentionPolicy,
    RetentionRequest,
    RetentionService,
    StaticRetentionPolicyReader,
    build_retention_context,
)
from app.modules.customers.types import AddressType, DataExportStatus
from app.modules.identity.privacy import AccountClosureError
from app.modules.identity.security_events import (
    InMemorySecurityEventSink,
    InMemorySecurityMetrics,
    LoggingSecurityEventSink,
    SecurityEventCode,
    SecurityEventResult,
    SecurityObservability,
    hash_signal,
)
from tests.fakes.iam import RecordingAuditWriter
from tests.fakes.privacy import (
    COUNTRY_ID,
    NOW,
    PASSWORD,
    USER_A,
    actor,
    build_closure_world,
    build_export_world,
)

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?")
_DIGEST = re.compile(r"[A-Za-z0-9._-]{1,32}\$hmac-sha256\$[A-Za-z0-9_-]{43}")
_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\+\d{8,15}|(?<!\d)\d{10,}(?!\d)"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    "token": re.compile(r"[A-Za-z0-9_-]{40,}"),
    "otp": re.compile(r"(?<![\d.])\d{6}(?![\d.])"),
    "password_hash": re.compile(r"\$argon2"),
    "address": re.compile(r"\b\d+\s+\w+\s+(?:Road|Street|Lane|Avenue)\b", re.IGNORECASE),
}


def find_leaks(payload: object) -> set[str]:
    """Return the kinds of sensitive patterns found in a payload."""
    text = json.dumps(payload, default=str, sort_keys=True)
    for harmless in (_UUID, _TIMESTAMP, _DIGEST):
        text = harmless.sub("", text)
    return {name for name, pattern in _PATTERNS.items() if pattern.search(text)}


def _audit_payloads(audit: RecordingAuditWriter) -> list[dict[str, object]]:
    return [dataclasses.asdict(record[0]) for record in audit.records]


def test_scanner_detects_planted_values_and_ignores_safe_identifiers() -> None:
    """Prove the scanner finds each leak kind and passes IDs and digests."""
    planted = {
        "email": "buyer@example.com",
        "phone": "+9779841234567",
        "jwt": "eyJhbGciOi.eyJzdWIiOi.sig",
        "token": "t" * 43,
        "otp": "code 123456 sent",
        "password_hash": "$argon2id$v=19$m=1",
        "address": "12 Lake Road",
    }
    for kind, value in planted.items():
        assert kind in find_leaks({"value": value}), kind
    digest = DigestService(active_key_id="v1", peppers={"v1": b"p" * 32}).digest("x", "y")
    safe = {"id": str(UUID(int=7)), "at": NOW.isoformat(), "signal": digest, "n": 3}
    assert find_leaks(safe) == set()


def test_closure_audit_and_errors_contain_no_pii() -> None:
    """Keep account-closure audit events and failures free of PII."""
    world = build_closure_world()
    token = world.service.request(actor(USER_A), PASSWORD, "s" * 40).close_grant
    world.service.confirm(actor(USER_A), token)
    payloads = _audit_payloads(world.audit)
    assert {item["action"] for item in payloads} >= {
        "iam.account.closure_requested",
        "iam.account.closed",
        "iam.customer.pseudonymized",
    }
    assert find_leaks(payloads) == set()
    fresh = build_closure_world()
    with pytest.raises(AccountClosureError) as failure:
        fresh.service.request(actor(USER_A), "wrong-password", "s" * 40)
    assert find_leaks(str(failure.value)) == set()


def _export_flow() -> tuple[object, list[dict[str, object]], UUID]:
    world = build_export_world()
    service = world.service
    request = service.request_export(actor(USER_A), "NP", "idem-key", "v" * 40)
    context = DatabaseRequestContext(
        authorization_mode=AuthorizationMode.SYSTEM_JOB,
        job_purpose="CUSTOMER_EXPORT",
        effective_user_id=request.user_id,
        effective_country_id=request.country_id,
    )
    service.claim(request.export_request_id, context)
    service.complete(request.export_request_id, context, {"sections": ["account", "consents"]})
    grant = service.create_download_grant(actor(USER_A), request.export_request_id)
    service.download(actor(USER_A), request.export_request_id, grant)
    return world, _audit_payloads(world.audit), request.export_request_id


def test_export_audit_status_and_download_contain_no_pii_or_references() -> None:
    """Keep export audit evidence, status views, and download output clean."""
    world, payloads, request_id = _export_flow()
    actions = {item["action"] for item in payloads}
    assert {
        "iam.data_export.requested",
        "iam.data_export.generated",
        "iam.data_export.downloaded",
    } <= actions
    assert find_leaks(payloads) == set()
    view = world.service.get_status(actor(USER_A), request_id)
    assert "artifact" not in repr(view) and view.status is DataExportStatus.DOWNLOADED
    assert "secret-store-ref" not in json.dumps(payloads, default=str)


@pytest.mark.parametrize(
    "payload",
    [
        {"password_hash": "x"},
        {"account": {"refresh_token": "x"}},
        {"sessions": [{"otp_code": "1"}]},
        {"email": "x"},
    ],
)
def test_export_generation_rejects_credential_bearing_payloads(
    payload: dict[str, object],
) -> None:
    """Refuse to store export payloads that carry credential-like keys."""
    world = build_export_world()
    request = world.service.request_export(actor(USER_A), "NP", "k", "v" * 40)
    context = DatabaseRequestContext(
        authorization_mode=AuthorizationMode.SYSTEM_JOB,
        job_purpose="CUSTOMER_EXPORT",
        effective_user_id=request.user_id,
        effective_country_id=request.country_id,
    )
    world.service.claim(request.export_request_id, context)
    with pytest.raises(CustomerExportError):
        world.service.complete(request.export_request_id, context, payload)
    assert request.status is DataExportStatus.PROCESSING


def test_security_events_for_every_code_contain_no_pii() -> None:
    """Scan one event per code with hashed signals and safe attributes."""
    digests = DigestService(active_key_id="v1", peppers={"v1": b"p" * 32})
    sink = InMemorySecurityEventSink()
    observability = SecurityObservability(
        sink=sink, metrics=InMemorySecurityMetrics(), clock=lambda: NOW
    )
    for code in SecurityEventCode:
        observability.emit(
            code,
            SecurityEventResult.SUCCESS,
            UUID(int=9),
            country_id=COUNTRY_ID,
            signals={
                "ip_hash": hash_signal(digests, "ip_hash", "203.0.113.9"),
                "identifier_hash": hash_signal(digests, "identifier_hash", "buyer@example.com"),
            },
            attributes={"channel": "EMAIL", "permission": "iam.assignment.manage"},
        )
    assert len(sink.events) == len(SecurityEventCode)
    payloads = [event.as_dict() for event in sink.events]
    assert find_leaks(payloads) == set()
    assert "203.0.113.9" not in json.dumps(payloads)


def test_security_event_log_lines_contain_no_pii(caplog: pytest.LogCaptureFixture) -> None:
    """Scan the structured log output of the logging sink."""
    digests = DigestService(active_key_id="v1", peppers={"v1": b"p" * 32})
    observability = SecurityObservability(
        sink=LoggingSecurityEventSink(), metrics=InMemorySecurityMetrics(), clock=lambda: NOW
    )
    with caplog.at_level("INFO", logger="sukilo.security_events"):
        observability.emit(
            SecurityEventCode.LOGIN_FAILED,
            SecurityEventResult.FAILURE,
            UUID(int=9),
            signals={"ip_hash": hash_signal(digests, "ip_hash", "203.0.113.9")},
        )
    assert caplog.records and find_leaks([record.getMessage() for record in caplog.records]) == set()


def test_retention_evidence_and_audit_contain_no_pii() -> None:
    """Scan retention evidence, observer output, and audit references."""
    address = Address(
        address_id=UUID(int=501),
        user_id=USER_A,
        country_id=COUNTRY_ID,
        recipient_name="Recipient One",
        phone_e164="+9779841111111",
        address_line_1="1 Lake Road",
        address_type=AddressType.HOME,
        deleted_at=NOW - timedelta(days=400),
        version=1,
        is_default_shipping=False,
        is_default_billing=False,
    )
    policy = RetentionPolicy(
        country_id=COUNTRY_ID,
        data_class=DATA_CLASS_CUSTOMER_ADDRESS,
        policy_version="np-1",
        retention_days=365,
        approved_fields=frozenset({"recipient_name", "phone_e164", "address_line_1"}),
        approved=True,
        change_ticket="CHG-1",
    )
    audit = RecordingAuditWriter()
    observer = InMemoryRetentionObserver()
    store = SimpleNamespace(select_batch=lambda *args, **kwargs: [address])
    service = RetentionService(
        policies=StaticRetentionPolicyReader(policies=[policy]),
        store=store,
        audit_writer=audit,
        transaction=DatabaseTransaction(SimpleNamespace()),
        observer=observer,
        clock=lambda: NOW,
        context_applier=lambda session, context: None,
    )
    request = RetentionRequest(
        country_id=COUNTRY_ID,
        data_class=DATA_CLASS_CUSTOMER_ADDRESS,
        correlation_id=UUID(int=3),
        execute=True,
        batch_size=5,
    )
    evidence = service.run(request, build_retention_context(COUNTRY_ID, UUID(int=3)))
    assert evidence.changed == 1
    combined = [dataclasses.asdict(evidence), observer.alerts, _audit_payloads(audit)]
    assert find_leaks(combined) == set()
    assert "Recipient One" not in json.dumps(combined, default=str)
