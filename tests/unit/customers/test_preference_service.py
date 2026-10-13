"""Test customer preference service invariants."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.customers.service import (
    PreferenceConflictError,
    PreferenceService,
    PreferenceValidationError,
    SetPreferences,
)
from app.modules.customers.models import UserPreference
from app.ports.audit import AuditEventInput
from app.ports.country import CountryReference


NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
SESSION_ID = UUID(int=2)
CORRELATION_ID = UUID(int=3)
COUNTRY_ID = UUID(int=4)


def _actor() -> AuthContext:
    """Build an authenticated customer context."""
    return AuthContext(
        user_id=USER_ID,
        session_id=SESSION_ID,
        auth_version=1,
        authentication_methods=("pwd",),
        authenticated_at=NOW,
        correlation_id=CORRELATION_ID,
    )


class CountriesFake:
    """Validate one exact preference combination."""

    def __init__(self) -> None:
        """Initialize enabled country capabilities."""
        self.operation_enabled = True

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve the configured country."""
        if code != "NP":
            raise KeyError(code)
        return CountryReference(COUNTRY_ID, "NP", "ACTIVE")

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Require the preference operation."""
        return (
            country_id == COUNTRY_ID
            and operation == "CUSTOMER_PREFERENCE"
            and self.operation_enabled
        )

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Accept only Nepal's configured locale."""
        return country_id == COUNTRY_ID and locale_code == "en-NP"

    def is_currency_enabled(self, country_id: UUID, currency_code: str) -> bool:
        """Accept only Nepal's configured currency."""
        return country_id == COUNTRY_ID and currency_code == "NPR"

    def resolve_country_id(self, country_id: UUID) -> CountryReference:
        """Reverse one stable country identifier."""
        if country_id != COUNTRY_ID:
            raise KeyError(country_id)
        return CountryReference(COUNTRY_ID, "NP", "ACTIVE")


class PreferenceRepositoryFake:
    """Store one mutable preference row."""

    def __init__(self, row: UserPreference | None = None) -> None:
        """Initialize repository state."""
        self.row = row
        self.flush_count = 0

    def get(self, user_id: UUID, *, for_update: bool = False):
        """Return the owned preference row."""
        del for_update
        return self.row if user_id == USER_ID else None

    def add(self, preference: UserPreference) -> None:
        """Stage one preference row."""
        self.row = preference

    def flush(self) -> None:
        """Record a transaction flush."""
        self.flush_count += 1


class AuditFake:
    """Capture staged redacted events."""

    def __init__(self) -> None:
        """Initialize empty audit storage."""
        self.records: list[tuple[AuditEventInput, DatabaseTransaction]] = []

    def stage(
        self,
        event: AuditEventInput,
        transaction: DatabaseTransaction,
    ) -> None:
        """Capture one event."""
        self.records.append((event, transaction))


def _service(
    repository: PreferenceRepositoryFake,
) -> tuple[PreferenceService, AuditFake, CountriesFake, DatabaseTransaction]:
    """Build the preference service with deterministic adapters."""
    audit = AuditFake()
    countries = CountriesFake()
    transaction = DatabaseTransaction(object())
    service = PreferenceService(
        repository=repository,
        countries=countries,
        audit_writer=audit,
        transaction=transaction,
        clock=lambda: NOW,
    )
    return service, audit, countries, transaction


def _command(expected_version: int | None = None) -> SetPreferences:
    """Build a valid complete preference command."""
    return SetPreferences(
        actor=_actor(),
        country_code="NP",
        locale_code="en-NP",
        currency_code="NPR",
        expected_version=expected_version,
    )


def test_set_preferences_creates_one_complete_validated_row() -> None:
    """Create one row after validating the exact country combination."""
    repository = PreferenceRepositoryFake()
    service, _, _, _ = _service(repository)
    view = service.set_preferences(_command())
    assert view.user_id == USER_ID
    assert view.country_id == COUNTRY_ID
    assert view.country_code == "NP"
    assert view.locale_code == "en-NP"
    assert view.currency_code == "NPR"
    assert view.marketing_email is False
    assert view.marketing_sms is False
    assert view.version == 1
    assert repository.flush_count == 1


@pytest.mark.parametrize(
    ("country_code", "locale_code", "currency_code"),
    [
        ("", "en-NP", "NPR"),
        ("NP", "", "NPR"),
        ("NP", "en-NP", ""),
        ("NP", "ar-AE", "NPR"),
        ("NP", "en-NP", "AED"),
    ],
)
def test_set_preferences_rejects_incomplete_or_mismatched_combinations(
    country_code: str,
    locale_code: str,
    currency_code: str,
) -> None:
    """Reject incomplete and cross-country preference combinations."""
    service, _, _, _ = _service(PreferenceRepositoryFake())
    command = SetPreferences(
        actor=_actor(),
        country_code=country_code,
        locale_code=locale_code,
        currency_code=currency_code,
        expected_version=None,
    )
    with pytest.raises(PreferenceValidationError):
        service.set_preferences(command)


def test_set_preferences_rejects_disabled_preference_operation() -> None:
    """Reject countries not enabled for preference selection."""
    service, _, countries, _ = _service(PreferenceRepositoryFake())
    countries.operation_enabled = False
    with pytest.raises(PreferenceValidationError):
        service.set_preferences(_command())


def test_set_preferences_rejects_stale_expected_version() -> None:
    """Reject an optimistic update made from stale state."""
    row = UserPreference(
        user_id=USER_ID,
        preferred_country_id=COUNTRY_ID,
        preferred_locale_code="en-NP",
        preferred_currency_code="NPR",
        marketing_email=True,
        marketing_sms=True,
        created_at=NOW,
        updated_at=NOW,
        version=3,
    )
    service, _, _, _ = _service(PreferenceRepositoryFake(row))
    with pytest.raises(PreferenceConflictError):
        service.set_preferences(_command(expected_version=2))


def test_set_preferences_preserves_marketing_and_audits_field_names_only() -> None:
    """Keep consent projections unchanged and audit no preference values."""
    row = UserPreference(
        user_id=USER_ID,
        preferred_country_id=UUID(int=9),
        preferred_locale_code="ar-AE",
        preferred_currency_code="AED",
        marketing_email=True,
        marketing_sms=True,
        created_at=NOW,
        updated_at=NOW,
        version=3,
    )
    service, audit, _, transaction = _service(PreferenceRepositoryFake(row))
    view = service.set_preferences(_command(expected_version=3))
    assert view.marketing_email is True
    assert view.marketing_sms is True
    assert view.version == 4
    event, audit_transaction = audit.records[0]
    assert event.before_data == {}
    assert event.after_data == {
        "changed_fields": (
            "preferred_country_id",
            "preferred_locale_code",
            "preferred_currency_code",
        )
    }
    assert audit_transaction is transaction
    assert "NP" not in repr(event.after_data)
    assert "NPR" not in repr(event.after_data)


def test_get_preferences_returns_only_authenticated_users_row() -> None:
    """Read the single preference row for the authenticated account."""
    row = UserPreference(
        user_id=USER_ID,
        preferred_country_id=COUNTRY_ID,
        preferred_locale_code="en-NP",
        preferred_currency_code="NPR",
        marketing_email=False,
        marketing_sms=False,
        created_at=NOW,
        updated_at=NOW,
        version=1,
    )
    service, _, _, _ = _service(PreferenceRepositoryFake(row))
    view = service.get_preferences(_actor())
    assert view is not None
    assert view.user_id == USER_ID
    assert view.country_code == "NP"


def test_get_preferences_fails_safely_when_country_reference_missing() -> None:
    """Omit country code when the stored country identifier is unknown."""
    row = UserPreference(
        user_id=USER_ID,
        preferred_country_id=UUID(int=99),
        preferred_locale_code="en-NP",
        preferred_currency_code="NPR",
        marketing_email=False,
        marketing_sms=False,
        created_at=NOW,
        updated_at=NOW,
        version=1,
    )
    service, _, _, _ = _service(PreferenceRepositoryFake(row))
    view = service.get_preferences(_actor())
    assert view is not None
    assert view.country_code is None


def test_preference_command_does_not_expose_marketing_flags() -> None:
    """Prevent preference callers from changing consent projections."""
    values = {
        "actor": _actor(),
        "country_code": "NP",
        "locale_code": "en-NP",
        "currency_code": "NPR",
        "expected_version": None,
        "marketing_email": True,
    }
    with pytest.raises(TypeError):
        SetPreferences(**values)
