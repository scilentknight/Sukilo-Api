"""Test account service and repository security behavior."""

import importlib.util
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects import postgresql


def _load_identity_module(name: str) -> ModuleType:
    """Load an identity module after asserting that it exists."""
    module_name = f"app.modules.identity.{name}"
    assert importlib.util.find_spec(module_name) is not None
    return __import__(module_name, fromlist=["AccountService"])


class FakeAccountRepository:
    """Store account service writes without a database."""

    def __init__(
        self,
        *,
        duplicate: bool = False,
        account: Any = None,
        flush_error: IntegrityError | None = None,
    ) -> None:
        """Initialize the repository state."""
        self.duplicate = duplicate
        self.account = account
        self.flush_error = flush_error
        self.accounts: list[Any] = []
        self.identities: list[Any] = []

    def get_by_id(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> Any:
        """Return the configured account."""
        del user_id, for_update
        return self.account

    def identifier_exists(
        self,
        email: str | None,
        phone_e164: str | None,
    ) -> bool:
        """Return the configured duplicate result."""
        return self.duplicate

    def add(self, account: Any) -> None:
        """Record an account write."""
        self.accounts.append(account)

    def add_identity(self, identity: Any) -> None:
        """Record an identity write."""
        self.identities.append(identity)

    def flush(self) -> None:
        """Raise the configured persistence conflict."""
        if self.flush_error is not None:
            raise self.flush_error


class RecordingSession:
    """Capture a real SQLAlchemy statement for compilation."""

    def __init__(self) -> None:
        """Initialize the captured statement."""
        self.statement = None

    def scalar(self, statement: Any) -> None:
        """Capture a scalar query and return no row."""
        self.statement = statement
        return None


def test_create_pending_account_normalizes_and_stages_identity() -> None:
    """Create one pending account and its local identity without committing."""
    service_module = _load_identity_module("service")
    repository = FakeAccountRepository()
    service = service_module.AccountService(repository)
    account = service.create_pending_local_account(
        email=" Buyer@Example.COM ",
        phone_e164=None,
        password_hash="encoded-password-hash",
    )
    assert account.email == "buyer@example.com"
    assert account.status.value == "PENDING"
    assert len(repository.accounts) == 1
    assert len(repository.identities) == 1
    identity = repository.identities[0]
    assert identity.user_id == account.user_id
    assert identity.provider.value == "LOCAL"
    assert identity.provider_subject == str(account.user_id)


def test_create_pending_account_normalizes_optional_phone() -> None:
    """Normalize and stage an optional phone identifier."""
    service_module = _load_identity_module("service")
    repository = FakeAccountRepository()
    service = service_module.AccountService(repository)
    account = service.create_pending_local_account(
        email="buyer@example.com",
        phone_e164="+977 9841234567",
        password_hash="encoded-password-hash",
    )
    assert account.phone_e164 == "+9779841234567"


@pytest.mark.parametrize(
    ("email", "password_hash"),
    [
        ("", "encoded-password-hash"),
        ("buyer@example.com", ""),
    ],
)
def test_create_pending_account_requires_email_and_password(
    email: str,
    password_hash: str,
) -> None:
    """Reject incomplete initial registration credentials."""
    service_module = _load_identity_module("service")
    service = service_module.AccountService(FakeAccountRepository())
    with pytest.raises(service_module.AccountValidationError) as exc_info:
        service.create_pending_local_account(
            email=email,
            phone_e164=None,
            password_hash=password_hash,
        )
    assert exc_info.value.code == "IAM_REGISTRATION_CREDENTIALS_REQUIRED"


def test_create_pending_account_maps_duplicate_to_safe_conflict() -> None:
    """Return a stable conflict without exposing the duplicate identifier."""
    service_module = _load_identity_module("service")
    service = service_module.AccountService(
        FakeAccountRepository(duplicate=True),
    )
    with pytest.raises(service_module.AccountConflictError) as exc_info:
        service.create_pending_local_account(
            email="buyer@example.com",
            phone_e164=None,
            password_hash="encoded-password-hash",
        )
    assert exc_info.value.code == "IAM_IDENTIFIER_CONFLICT"
    assert "buyer@example.com" not in str(exc_info.value)


def test_create_pending_account_maps_flush_race_to_safe_conflict() -> None:
    """Translate a database uniqueness race into the domain conflict."""
    service_module = _load_identity_module("service")
    integrity_error = IntegrityError(
        "INSERT INTO iam.user_account",
        {},
        Exception("duplicate key"),
    )
    repository = FakeAccountRepository(flush_error=integrity_error)
    service = service_module.AccountService(repository)
    with pytest.raises(service_module.AccountConflictError) as exc_info:
        service.create_pending_local_account(
            email="buyer@example.com",
            phone_e164=None,
            password_hash="encoded-password-hash",
        )
    assert exc_info.value.code == "IAM_IDENTIFIER_CONFLICT"
    assert "buyer@example.com" not in str(exc_info.value)


def test_get_account_returns_existing_account() -> None:
    """Return the configured account by stable ID."""
    service_module = _load_identity_module("service")
    account = object()
    service = service_module.AccountService(
        FakeAccountRepository(account=account),
    )
    assert service.get_account(uuid4()) is account


def test_get_account_maps_missing_row_to_safe_error() -> None:
    """Hide missing account details behind a stable error."""
    service_module = _load_identity_module("service")
    service = service_module.AccountService(FakeAccountRepository())
    with pytest.raises(service_module.AccountNotFoundError) as exc_info:
        service.get_account(uuid4())
    assert exc_info.value.code == "IAM_ACCOUNT_NOT_FOUND"


def test_authentication_allows_active_account() -> None:
    """Allow an active account to authenticate."""
    service_module = _load_identity_module("service")
    models = _load_identity_module("models")
    types = _load_identity_module("types")
    service = service_module.AccountService(FakeAccountRepository())
    active = models.UserAccount(status=types.AccountStatus.ACTIVE)
    service.assert_account_can_authenticate(active)


@pytest.mark.parametrize("status_name", ["PENDING", "SUSPENDED", "CLOSED"])
def test_authentication_rejects_non_active_account(status_name: str) -> None:
    """Reject every non-active account lifecycle state."""
    service_module = _load_identity_module("service")
    models = _load_identity_module("models")
    types = _load_identity_module("types")
    service = service_module.AccountService(FakeAccountRepository())
    account = models.UserAccount(status=types.AccountStatus[status_name])
    with pytest.raises(service_module.AccountValidationError) as exc_info:
        service.assert_account_can_authenticate(account)
    assert exc_info.value.code == "IAM_ACCOUNT_UNAVAILABLE"


def test_email_lookup_compiles_untrusted_value_as_bound_parameter() -> None:
    """Keep hostile identifier text outside generated SQL."""
    repository_module = _load_identity_module("repository")
    hostile_email = "buyer@example.com' OR 1=1 --"
    session = RecordingSession()
    repository = repository_module.AccountRepository(session)
    repository.get_by_email(hostile_email)
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert hostile_email not in str(compiled)
    assert hostile_email in compiled.params.values()


def test_phone_lookup_compiles_untrusted_value_as_bound_parameter() -> None:
    """Keep hostile phone text outside generated SQL."""
    repository_module = _load_identity_module("repository")
    hostile_phone = "+9779841234567' OR 1=1 --"
    session = RecordingSession()
    repository = repository_module.AccountRepository(session)
    repository.get_by_phone(hostile_phone)
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert hostile_phone not in str(compiled)
    assert hostile_phone in compiled.params.values()


def test_id_lookup_uses_bound_parameter_and_optional_row_lock() -> None:
    """Bind UUID account lookups and add a requested row lock."""
    repository_module = _load_identity_module("repository")
    user_id = uuid4()
    session = RecordingSession()
    repository = repository_module.AccountRepository(session)
    repository.get_by_id(user_id, for_update=True)
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert str(user_id) not in str(compiled)
    assert user_id in compiled.params.values()
    assert "FOR UPDATE" in str(compiled)
