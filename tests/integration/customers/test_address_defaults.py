"""Test address default SQL and deterministic behavior without a live database."""

from uuid import UUID

from sqlalchemy.dialects import postgresql

from app.modules.customers.repository import CustomerRepository
from app.modules.customers.types import DefaultKind

USER_ID = UUID(int=1)
COUNTRY_ID = UUID(int=2)


class SessionFake:
    """Capture SQLAlchemy address statements."""

    def __init__(self) -> None:
        """Initialize captured statements."""
        self.statements: list[object] = []

    def execute(self, statement: object) -> None:
        """Capture an executed statement."""
        self.statements.append(statement)

    def scalars(self, statement: object) -> list[object]:
        """Capture a collection statement."""
        self.statements.append(statement)
        return []


def _compile(statement: object) -> tuple[str, dict[str, object]]:
    """Compile one statement for PostgreSQL."""
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_default_scope_uses_parameterized_transaction_advisory_lock() -> None:
    """Serialize an empty default scope with a bound stable lock key."""
    session = SessionFake()
    repository = CustomerRepository(session)
    repository.lock_default_scope(USER_ID, COUNTRY_ID, DefaultKind.SHIPPING)
    sql, params = _compile(session.statements[0])
    assert "pg_advisory_xact_lock" in sql
    assert len(params) == 1
    assert isinstance(next(iter(params.values())), int)
    assert str(USER_ID) not in sql
    assert str(COUNTRY_ID) not in sql


def test_default_kinds_use_distinct_stable_lock_keys() -> None:
    """Use repeatable and kind-specific advisory lock keys."""
    first = SessionFake()
    second = SessionFake()
    repository = CustomerRepository(first)
    repository.lock_default_scope(USER_ID, COUNTRY_ID, DefaultKind.SHIPPING)
    repository.lock_default_scope(USER_ID, COUNTRY_ID, DefaultKind.BILLING)
    CustomerRepository(second).lock_default_scope(
        USER_ID,
        COUNTRY_ID,
        DefaultKind.SHIPPING,
    )
    first_shipping = _compile(first.statements[0])[1]
    first_billing = _compile(first.statements[1])[1]
    second_shipping = _compile(second.statements[0])[1]
    assert first_shipping == second_shipping
    assert first_shipping != first_billing


def test_active_default_rows_lock_exact_owner_and_country() -> None:
    """Lock all active rows inside the exact owner-country scope."""
    session = SessionFake()
    repository = CustomerRepository(session)
    repository.lock_active_addresses(USER_ID, COUNTRY_ID)
    sql, params = _compile(session.statements[0])
    assert "FOR UPDATE" in sql
    assert "address.user_id" in sql
    assert "address.country_id" in sql
    assert "address.deleted_at IS NULL" in sql
    assert USER_ID in params.values()
    assert COUNTRY_ID in params.values()
