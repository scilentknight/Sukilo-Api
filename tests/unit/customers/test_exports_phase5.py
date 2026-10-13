"""Test customer export ownership, idempotency, and one-time download."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.customers.exports import (
    CustomerExportError,
    CustomerExportNotFoundError,
    CustomerExportService,
)
from app.modules.customers.models import DataExportRequest
from app.modules.customers.types import DataExportStatus
from app.modules.identity.grants import AuthGrantError
from app.ports.country import CountryReference
from tests.fakes.iam import RecordingAuditWriter

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
OTHER_USER_ID = UUID(int=2)
SESSION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)
CORRELATION_ID = UUID(int=5)
REQUEST_ID = UUID(int=6)


class _Repository:
    def __init__(self) -> None:
        self.requests: dict[UUID, DataExportRequest] = {}

    def get_export_by_key(
        self,
        user_id: UUID,
        country_id: UUID,
        idempotency_key: str,
    ) -> DataExportRequest | None:
        return next(
            (
                item
                for item in self.requests.values()
                if item.user_id == user_id
                and item.country_id == country_id
                and item.idempotency_key == idempotency_key
            ),
            None,
        )

    def add_export(self, request: DataExportRequest) -> None:
        self.requests[request.export_request_id] = request

    def get_owned_export(
        self,
        user_id: UUID,
        export_request_id: UUID,
        *,
        for_update: bool = False,
    ) -> DataExportRequest | None:
        del for_update
        request = self.requests.get(export_request_id)
        return request if request is not None and request.user_id == user_id else None

    def flush(self) -> None:
        return None


class _Grants:
    def __init__(self) -> None:
        self.consumed: set[str] = set()
        self.created_subjects: dict[str, str | None] = {}
        self.consume_count = 0

    def create(self, **values: object) -> str:
        token = f"grant-{len(self.created_subjects):035d}"
        self.created_subjects[token] = values.get("subject_hash")
        return token

    def consume(self, raw_token: str, **bindings: object) -> object:
        self.consume_count += 1
        if raw_token in self.consumed:
            raise AuthGrantError("Authorization grant is unavailable.")
        expected = self.created_subjects.get(raw_token)
        if (
            raw_token in self.created_subjects
            and expected != bindings.get("subject_hash")
        ):
            raise AuthGrantError("Authorization grant binding is invalid.")
        self.consumed.add(raw_token)
        return SimpleNamespace()


class _GrantRepository:
    def revoke_subject_grants(self, *args: object) -> None:
        del args


class _Countries:
    def resolve_country(self, code: str) -> CountryReference:
        assert code == "NP"
        return CountryReference(country_id=COUNTRY_ID, code="NP", status="ACTIVE")


class _Artifacts:
    def __init__(self) -> None:
        self.references: list[str] = []

    def create_download_reference(self, artifact_reference: str) -> str:
        self.references.append(artifact_reference)
        return "d" * 43


def _actor(user_id: UUID = USER_ID) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("password", "otp"),
        authenticated_at=NOW,
        correlation_id=CORRELATION_ID,
    )


def _service() -> tuple[
    CustomerExportService,
    _Repository,
    _Grants,
    RecordingAuditWriter,
]:
    repository = _Repository()
    grants = _Grants()
    audit = RecordingAuditWriter()
    service = CustomerExportService(
        repository=repository,
        accounts=SimpleNamespace(),
        grant_repository=_GrantRepository(),
        grants=grants,
        otp=SimpleNamespace(),
        passwords=SimpleNamespace(),
        digests=DigestService(active_key_id="v1", peppers={"v1": b"p" * 32}),
        countries=_Countries(),
        artifacts=_Artifacts(),
        audit_writer=audit,
        transaction=DatabaseTransaction(SimpleNamespace()),
        clock=lambda: NOW,
    )
    return service, repository, grants, audit


def _ready_request() -> DataExportRequest:
    return DataExportRequest(
        export_request_id=REQUEST_ID,
        user_id=USER_ID,
        country_id=COUNTRY_ID,
        idempotency_key="request-key",
        status=DataExportStatus.READY,
        artifact_reference="secret-storage-reference",
        artifact_expires_at=NOW + timedelta(minutes=30),
        requested_at=NOW,
        completed_at=NOW,
        correlation_id=CORRELATION_ID,
        version=1,
    )


def test_export_request_is_idempotent_without_reconsuming_grant() -> None:
    """Return the original request for the same owner, country, and key."""
    service, repository, grants, _ = _service()
    first = service.request_export(_actor(), "NP", "same-key", "v" * 40)
    second = service.request_export(_actor(), "NP", "same-key", "different-grant")
    assert second is first
    assert len(repository.requests) == 1
    assert grants.consume_count == 1


def test_wrong_owner_status_is_hidden_as_not_found() -> None:
    """Hide another customer's export request."""
    service, repository, _, _ = _service()
    repository.add_export(_ready_request())
    with pytest.raises(CustomerExportNotFoundError):
        service.get_status(_actor(OTHER_USER_ID), REQUEST_ID)


def test_download_grant_is_request_bound_and_single_use() -> None:
    """Reject cross-request binding and replay the same opaque grant."""
    service, repository, grants, _ = _service()
    repository.add_export(_ready_request())
    token = service.create_download_grant(_actor(), REQUEST_ID)
    subject_hash = grants.created_subjects[token]
    grants.created_subjects[token] = "wrong-request-binding"
    with pytest.raises(AuthGrantError):
        service.download(_actor(), REQUEST_ID, token)
    grants.created_subjects[token] = subject_hash
    reference = service.download(_actor(), REQUEST_ID, token)
    assert reference == "d" * 43
    with pytest.raises(CustomerExportError):
        service.download(_actor(), REQUEST_ID, token)


def test_only_one_download_can_transition_ready_request() -> None:
    """Allow one winner after the locked row changes to downloaded."""
    service, repository, _, _ = _service()
    repository.add_export(_ready_request())
    first = service.create_download_grant(_actor(), REQUEST_ID)
    second = service.create_download_grant(_actor(), REQUEST_ID)
    service.download(_actor(), REQUEST_ID, second)
    with pytest.raises(CustomerExportError):
        service.download(_actor(), REQUEST_ID, first)
    assert repository.requests[REQUEST_ID].status == DataExportStatus.DOWNLOADED


def test_export_audit_payloads_exclude_sensitive_values() -> None:
    """Keep export audit evidence free of PII and credentials."""
    service, _, _, audit = _service()
    service.request_export(_actor(), "NP", "safe-key", "v" * 40)
    serialized = repr([record[0] for record in audit.records]).casefold()
    for forbidden in ("email", "phone", "address", "password", "token", "otp"):
        assert forbidden not in serialized
