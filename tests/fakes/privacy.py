"""Provide in-memory closure and export worlds with two customers for tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from types import SimpleNamespace
from uuid import UUID

from app.core.auth_context import AuthContext
from app.core.database_context import DatabaseRequestContext
from app.core.security import DigestService
from app.core.transactions import DatabaseTransaction
from app.modules.customers.exports import CustomerExportService
from app.modules.customers.models import Address, DataExportRequest
from app.modules.customers.privacy import (
    AccountClosureService,
    DevelopmentRetentionPolicyReader,
)
from app.modules.customers.types import AddressType, DataExportStatus
from app.modules.identity.grants import AuthGrantError
from app.modules.identity.models import UserAccount
from app.modules.identity.passwords import PasswordHasher
from app.modules.identity.privacy import IdentityPrivacyService
from app.modules.identity.types import AccountStatus
from app.ports.country import CountryReference
from tests.fakes.iam import RecordingAuditWriter

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_A = UUID(int=1)
USER_B = UUID(int=2)
COUNTRY_ID = UUID(int=4)
CORRELATION_ID = UUID(int=5)
EXPORT_A = UUID(int=21)
EXPORT_B = UUID(int=22)
PASSWORD = "correct-password"
EMAILS = {USER_A: "alice@example.com", USER_B: "bob@example.com"}
PHONES = {USER_A: "+9779841111111", USER_B: "+9779842222222"}


@cache
def _password_hash() -> str:
    return PasswordHasher().hash_password(PASSWORD)


def actor(user_id: UUID, *, otp: bool = False) -> AuthContext:
    """Build an authenticated principal for one customer."""
    methods = ("password", "otp") if otp else ("password",)
    return AuthContext(
        user_id=user_id,
        session_id=UUID(int=100 + user_id.int),
        auth_version=1,
        authentication_methods=methods,
        authenticated_at=NOW,
        correlation_id=CORRELATION_ID,
    )


def make_address(number: int, user_id: UUID) -> Address:
    """Build one address owned by a customer."""
    return Address(
        address_id=UUID(int=500 + number),
        user_id=user_id,
        country_id=COUNTRY_ID,
        recipient_name=f"Recipient {user_id.int}",
        phone_e164=PHONES[user_id],
        address_line_1=f"{number} Lake Road",
        address_type=AddressType.HOME,
        version=1,
        is_default_shipping=False,
        is_default_billing=False,
    )


class _Accounts:
    def __init__(self) -> None:
        self.by_id = {
            user_id: UserAccount(
                user_id=user_id,
                email=EMAILS[user_id],
                phone_e164=PHONES[user_id],
                password_hash=_password_hash(),
                status=AccountStatus.ACTIVE,
                auth_version=1,
                version=1,
            )
            for user_id in (USER_A, USER_B)
        }
        self.requested: list[UUID] = []

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> UserAccount | None:
        del for_update
        self.requested.append(user_id)
        return self.by_id.get(user_id)

    def delete_identities_by_user(self, user_id: UUID) -> None:
        del user_id

    def flush(self) -> None:
        return None


class _OwnedGrants:
    """Bind each issued grant to its owner and reject any other user."""

    def __init__(self) -> None:
        self.owners: dict[str, UUID] = {}

    def create(self, **values: object) -> str:
        token = f"grant-{len(self.owners):035d}"
        self.owners[token] = values["user_id"]  # type: ignore[assignment]
        return token

    def consume(self, raw_token: str, **bindings: object) -> object:
        if self.owners.get(raw_token, bindings["user_id"]) != bindings["user_id"]:
            raise AuthGrantError("Authorization grant is unavailable.")
        return SimpleNamespace()


class _Noop:
    def revoke_all_by_user(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def invalidate_all_by_user(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def send_security(self, notification: object) -> None:
        del notification


class _CustomerRepository:
    def __init__(self, addresses: list[Address]) -> None:
        self.addresses = addresses
        self.locked_for: list[UUID] = []
        self.listed_for: list[UUID] = []
        self.consents: list[object] = []

    def list_country_ids_by_user(self, user_id: UUID) -> list[UUID]:
        self.listed_for.append(user_id)
        return [COUNTRY_ID]

    def lock_all_addresses_by_user(self, user_id: UUID) -> list[Address]:
        self.locked_for.append(user_id)
        return [item for item in self.addresses if item.user_id == user_id]

    def add_consent(self, consent: object) -> None:
        self.consents.append(consent)

    def flush(self) -> None:
        return None


@dataclass
class ClosureWorld:
    """Group closure collaborators for two customers."""

    service: AccountClosureService
    identity: IdentityPrivacyService
    accounts: _Accounts
    grants: _OwnedGrants
    repository: _CustomerRepository
    audit: RecordingAuditWriter
    contexts: list[DatabaseRequestContext] = field(default_factory=list)


def build_closure_world() -> ClosureWorld:
    """Build real closure services over in-memory fakes for users A and B."""
    accounts = _Accounts()
    grants = _OwnedGrants()
    audit = RecordingAuditWriter()
    transaction = DatabaseTransaction(SimpleNamespace())
    noop = _Noop()
    identity = IdentityPrivacyService(
        accounts=accounts,
        grant_repository=noop,
        otp_repository=noop,
        session_repository=noop,
        grants=grants,
        passwords=PasswordHasher(),
        digests=DigestService(active_key_id="v1", peppers={"v1": b"p" * 32}),
        notifications=noop,
        audit_writer=audit,
        transaction=transaction,
        clock=lambda: NOW,
    )
    repository = _CustomerRepository(
        [make_address(1, USER_A), make_address(2, USER_B)]
    )
    contexts: list[DatabaseRequestContext] = []
    service = AccountClosureService(
        identity=identity,
        repository=repository,
        retention=DevelopmentRetentionPolicyReader(),
        countries=SimpleNamespace(get_active_policy_version=lambda country, kind: "p1"),
        audit_writer=audit,
        transaction=transaction,
        clock=lambda: NOW,
        database=SimpleNamespace(),
        context_applier=lambda database, context: contexts.append(context),
    )
    return ClosureWorld(service, identity, accounts, grants, repository, audit, contexts)


class _ExportRepository:
    def __init__(self) -> None:
        self.requests: dict[UUID, DataExportRequest] = {}

    def get_export_by_key(self, user_id: UUID, country_id: UUID, key: str) -> None:
        del user_id, country_id, key
        return None

    def add_export(self, request: DataExportRequest) -> None:
        if request.version is None:
            request.version = 1
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

    def get_export_for_update(self, export_request_id: UUID) -> DataExportRequest | None:
        return self.requests.get(export_request_id)

    def flush(self) -> None:
        return None


class _ExportArtifacts:
    def store(self, export_request_id: UUID, payload: object) -> SimpleNamespace:
        del payload
        return SimpleNamespace(
            reference=f"secret-store-ref-{export_request_id}",
            expires_at=NOW + timedelta(hours=1),
        )

    def create_download_reference(self, artifact_reference: str) -> str:
        del artifact_reference
        return "d" * 43


class _Countries:
    def resolve_country(self, code: str) -> CountryReference:
        return CountryReference(country_id=COUNTRY_ID, code=code, status="ACTIVE")


@dataclass
class ExportWorld:
    """Group export collaborators for two customers."""

    service: CustomerExportService
    repository: _ExportRepository
    grants: _OwnedGrants
    audit: RecordingAuditWriter


def _ready_request(request_id: UUID, user_id: UUID) -> DataExportRequest:
    return DataExportRequest(
        export_request_id=request_id,
        user_id=user_id,
        country_id=COUNTRY_ID,
        idempotency_key=f"key-{request_id.int}",
        status=DataExportStatus.READY,
        artifact_reference=f"secret-store-ref-{request_id}",
        artifact_expires_at=NOW + timedelta(minutes=30),
        requested_at=NOW,
        completed_at=NOW,
        correlation_id=CORRELATION_ID,
        version=1,
    )


def build_export_world() -> ExportWorld:
    """Build the real export service with one READY export per customer."""
    repository = _ExportRepository()
    repository.add_export(_ready_request(EXPORT_A, USER_A))
    repository.add_export(_ready_request(EXPORT_B, USER_B))
    grants = _OwnedGrants()
    audit = RecordingAuditWriter()
    session = SimpleNamespace(execute=lambda *args, **kwargs: None)
    service = CustomerExportService(
        repository=repository,
        accounts=SimpleNamespace(),
        grant_repository=SimpleNamespace(revoke_subject_grants=lambda *args: None),
        grants=grants,
        otp=SimpleNamespace(),
        passwords=SimpleNamespace(),
        digests=DigestService(active_key_id="v1", peppers={"v1": b"p" * 32}),
        countries=_Countries(),
        artifacts=_ExportArtifacts(),
        audit_writer=audit,
        transaction=DatabaseTransaction(session),
        clock=lambda: NOW,
    )
    return ExportWorld(service, repository, grants, audit)
