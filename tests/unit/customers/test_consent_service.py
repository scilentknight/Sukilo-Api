"""Test append-only customer consent service invariants."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import ConsentRecord, UserPreference
from app.modules.customers.service import (
    ConsentService,
    ConsentValidationError,
    RecordConsent,
)
from app.modules.customers.types import ConsentSource, ConsentType
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
    """Return one country and active consent policies."""

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve Nepal."""
        if code != "NP":
            raise KeyError(code)
        return CountryReference(COUNTRY_ID, "NP", "ACTIVE")

    def get_active_policy_version(
        self,
        country_id: UUID,
        consent_type: str,
    ) -> str:
        """Return the active customer policy."""
        assert country_id == COUNTRY_ID
        return {
            ConsentType.TERMS.value: "terms-2",
            ConsentType.PRIVACY.value: "privacy-3",
            ConsentType.MARKETING_EMAIL.value: "marketing-1",
            ConsentType.MARKETING_SMS.value: "marketing-1",
            ConsentType.ANALYTICS.value: "analytics-1",
        }[consent_type]


class ConsentRepositoryFake:
    """Store append-only consents and one preference projection."""

    def __init__(self) -> None:
        """Initialize empty persistence."""
        self.consents: list[ConsentRecord] = []
        self.preference: UserPreference | None = None
        self.preference_lock_count = 0
        self.flush_count = 0

    def add_consent(self, consent: ConsentRecord) -> None:
        """Append one consent record."""
        self.consents.append(consent)

    def get_preference(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserPreference | None:
        """Return the preference projection and record locking."""
        assert user_id == USER_ID
        self.preference_lock_count += int(for_update)
        return self.preference

    def add_preference(self, preference: UserPreference) -> None:
        """Stage a new preference projection."""
        self.preference = preference

    def list_current_consents(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[ConsentRecord]:
        """Resolve current state by time and consent identifier."""
        matching = [
            row
            for row in self.consents
            if row.user_id == user_id and row.country_id == country_id
        ]
        latest: dict[ConsentType, ConsentRecord] = {}
        for row in sorted(
            matching,
            key=lambda value: (value.recorded_at, value.consent_id),
        ):
            latest[row.consent_type] = row
        return sorted(latest.values(), key=lambda value: value.consent_type.value)

    def list_consent_history(
        self,
        user_id: UUID,
        country_id: UUID,
        *,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ConsentRecord]:
        """Return one bounded newest-first page."""
        rows = [
            row
            for row in self.consents
            if row.user_id == user_id and row.country_id == country_id
        ]
        rows.sort(
            key=lambda value: (value.recorded_at, value.consent_id),
            reverse=True,
        )
        if cursor is not None:
            rows = [
                row
                for row in rows
                if (row.recorded_at, row.consent_id) < cursor
            ]
        return rows[:limit]

    def flush(self) -> None:
        """Record a transaction flush."""
        self.flush_count += 1


class AuditFake:
    """Capture staged consent events."""

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
    repository: ConsentRepositoryFake,
) -> tuple[ConsentService, AuditFake, DatabaseTransaction]:
    """Build a deterministic consent service."""
    audit = AuditFake()
    transaction = DatabaseTransaction(object())
    service = ConsentService(
        repository=repository,
        countries=CountriesFake(),
        audit_writer=audit,
        transaction=transaction,
        clock=lambda: NOW,
    )
    return service, audit, transaction


def _command(
    consent_type: ConsentType = ConsentType.TERMS,
    *,
    granted: bool = True,
    source: ConsentSource = ConsentSource.WEB,
    policy_version: str | None = None,
) -> RecordConsent:
    """Build a consent recording command."""
    versions = {
        ConsentType.TERMS: "terms-2",
        ConsentType.PRIVACY: "privacy-3",
        ConsentType.MARKETING_EMAIL: "marketing-1",
        ConsentType.MARKETING_SMS: "marketing-1",
        ConsentType.ANALYTICS: "analytics-1",
    }
    return RecordConsent(
        actor=_actor(),
        country_code="NP",
        consent_type=consent_type,
        policy_version=policy_version or versions[consent_type],
        granted=granted,
        source=source,
    )


def test_record_repeated_consent_appends_server_evidence_each_time() -> None:
    """Append a fresh server-timed row for every explicit action."""
    repository = ConsentRepositoryFake()
    service, _, _ = _service(repository)
    first = service.record(_command())
    second = service.record(_command())
    assert len(repository.consents) == 2
    assert first.consent_id != second.consent_id
    assert first.recorded_at == NOW
    assert second.recorded_at == NOW
    assert first.correlation_id == CORRELATION_ID
    assert second.correlation_id == CORRELATION_ID


def test_record_withdrawal_appends_without_mutating_history() -> None:
    """Preserve prior evidence when consent is withdrawn."""
    repository = ConsentRepositoryFake()
    service, _, _ = _service(repository)
    granted = service.record(_command(granted=True))
    withdrawn = service.record(_command(granted=False))
    assert [row.consent_id for row in repository.consents] == [
        granted.consent_id,
        withdrawn.consent_id,
    ]
    assert repository.consents[0].granted is True
    assert repository.consents[1].granted is False


def test_record_rejects_superseded_policy_and_admin_source() -> None:
    """Reject stale policy acknowledgements and non-customer sources."""
    service, _, _ = _service(ConsentRepositoryFake())
    with pytest.raises(ConsentValidationError):
        service.record(_command(policy_version="terms-1"))
    with pytest.raises(ConsentValidationError):
        service.record(_command(source=ConsentSource.ADMIN))


@pytest.mark.parametrize(
    ("consent_type", "attribute"),
    [
        (ConsentType.MARKETING_EMAIL, "marketing_email"),
        (ConsentType.MARKETING_SMS, "marketing_sms"),
    ],
)
def test_marketing_consent_updates_projection_in_same_unit_of_work(
    consent_type: ConsentType,
    attribute: str,
) -> None:
    """Create and version the matching marketing projection atomically."""
    repository = ConsentRepositoryFake()
    service, audit, transaction = _service(repository)
    service.record(_command(consent_type, granted=True))
    assert repository.preference is not None
    assert getattr(repository.preference, attribute) is True
    assert repository.preference.version == 1
    service.record(_command(consent_type, granted=False))
    assert getattr(repository.preference, attribute) is False
    assert repository.preference.version == 2
    assert repository.preference_lock_count == 2
    assert all(item[1] is transaction for item in audit.records)


@pytest.mark.parametrize(
    "consent_type",
    [ConsentType.TERMS, ConsentType.PRIVACY, ConsentType.ANALYTICS],
)
def test_non_marketing_consent_does_not_change_preferences(
    consent_type: ConsentType,
) -> None:
    """Keep preference marketing flags independent of other consent."""
    repository = ConsentRepositoryFake()
    repository.preference = UserPreference(
        user_id=USER_ID,
        preferred_country_id=COUNTRY_ID,
        preferred_locale_code="en-NP",
        preferred_currency_code="NPR",
        marketing_email=True,
        marketing_sms=True,
        created_at=NOW,
        updated_at=NOW,
        version=5,
    )
    service, _, _ = _service(repository)
    service.record(_command(consent_type, granted=False))
    assert repository.preference.marketing_email is True
    assert repository.preference.marketing_sms is True
    assert repository.preference.version == 5
    assert repository.preference_lock_count == 0


def test_current_state_uses_recorded_time_then_consent_id() -> None:
    """Choose the greater consent identifier when timestamps tie."""
    repository = ConsentRepositoryFake()
    service, _, _ = _service(repository)
    service.record(_command(granted=True))
    service.record(_command(granted=False))
    repository.consents[0].consent_id = UUID(int=10)
    repository.consents[1].consent_id = UUID(int=11)
    current = service.list_current(_actor(), "NP")
    assert len(current) == 1
    assert current[0].consent_id == UUID(int=11)
    assert current[0].granted is False


def test_history_is_bounded_and_cursor_paginated() -> None:
    """Return an explicit bounded consent evidence page."""
    repository = ConsentRepositoryFake()
    service, _, _ = _service(repository)
    for _ in range(3):
        service.record(_command())
    repository.consents[0].consent_id = UUID(int=10)
    repository.consents[1].consent_id = UUID(int=11)
    repository.consents[2].consent_id = UUID(int=12)
    first_page = service.list_history(_actor(), "NP", limit=2)
    assert [item.consent_id for item in first_page.items] == [
        UUID(int=12),
        UUID(int=11),
    ]
    assert first_page.next_cursor == (
        first_page.items[-1].recorded_at,
        first_page.items[-1].consent_id,
    )
    second_page = service.list_history(
        _actor(),
        "NP",
        limit=2,
        cursor=first_page.next_cursor,
    )
    assert [item.consent_id for item in second_page.items] == [UUID(int=10)]
    assert second_page.next_cursor is None


def test_history_does_not_advertise_cursor_on_terminal_page() -> None:
    """Avoid a next cursor when the page contains every remaining record."""
    repository = ConsentRepositoryFake()
    service, _, _ = _service(repository)
    for _ in range(2):
        service.record(_command())
    page = service.list_history(_actor(), "NP", limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is None


def test_consent_audit_is_redacted_and_transaction_bound() -> None:
    """Audit changed field names without policy or consent values."""
    service, audit, transaction = _service(ConsentRepositoryFake())
    service.record(_command(ConsentType.PRIVACY, granted=False))
    event, audit_transaction = audit.records[0]
    assert event.before_data == {}
    assert event.after_data == {
        "changed_fields": ("granted",),
        "consent_type": ConsentType.PRIVACY.value,
    }
    assert "privacy-3" not in repr(event)
    assert audit_transaction is transaction


def test_consent_command_does_not_accept_a_client_timestamp() -> None:
    """Require consent evidence timestamps to come from the server."""
    values = {
        "actor": _actor(),
        "country_code": "NP",
        "consent_type": ConsentType.TERMS,
        "policy_version": "terms-2",
        "granted": True,
        "source": ConsentSource.WEB,
        "recorded_at": NOW,
    }
    with pytest.raises(TypeError):
        RecordConsent(**values)
