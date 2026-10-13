"""Provide in-memory adapters for IAM tests."""

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

from app.core.transactions import DatabaseTransaction
from app.ports.audit import AuditEventInput
from app.ports.country import AddressPolicy, CountryReference

SENSITIVE_AUDIT_KEYS = frozenset(
    {
        "access_token",
        "otp",
        "password",
        "password_hash",
        "refresh_token",
        "token",
    },
)


@dataclass(frozen=True, slots=True)
class _CountryConfig:
    reference: CountryReference
    operations: frozenset[str]
    locales: frozenset[str]
    currencies: frozenset[str]
    geography_ids: frozenset[UUID]
    address_policy: AddressPolicy
    policy_versions: Mapping[str, str]


class InMemoryCountryReader:
    """Store explicit country capabilities for deterministic tests."""

    def __init__(self) -> None:
        """Initialize an empty country registry."""
        self._by_code: dict[str, _CountryConfig] = {}
        self._by_id: dict[UUID, _CountryConfig] = {}

    def add_country(
        self,
        reference: CountryReference,
        *,
        operations: set[str],
        locales: set[str],
        currencies: set[str],
        geography_ids: set[UUID],
        address_policy: AddressPolicy,
        policy_versions: Mapping[str, str],
    ) -> None:
        """Register one country and its capabilities.

        Args:
            reference: Stable country reference.
            operations: Enabled operation codes.
            locales: Enabled locale codes.
            currencies: Enabled currency codes.
            geography_ids: Geography IDs belonging to the country.
            address_policy: Validated address requirements.
            policy_versions: Active consent policy versions.
        """
        config = _CountryConfig(
            reference=reference,
            operations=frozenset(operations),
            locales=frozenset(locales),
            currencies=frozenset(currencies),
            geography_ids=frozenset(geography_ids),
            address_policy=address_policy,
            policy_versions=dict(policy_versions),
        )
        self._by_code[reference.code] = config
        self._by_id[reference.country_id] = config

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve a registered country code.

        Args:
            code: ISO alpha-2 country code.

        Returns:
            Registered country reference.
        """
        return self._by_code[code].reference

    def resolve_country_id(self, country_id: UUID) -> CountryReference:
        """Resolve a registered country identifier.

        Args:
            country_id: Stable country identifier.

        Returns:
            Registered country reference.
        """
        return self._by_id[country_id].reference

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Return whether an operation is enabled.

        Args:
            country_id: Stable country identifier.
            operation: Operation code.

        Returns:
            Whether the operation is enabled.
        """
        return operation in self._by_id[country_id].operations

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Return whether a locale is enabled.

        Args:
            country_id: Stable country identifier.
            locale_code: Locale code.

        Returns:
            Whether the locale is enabled.
        """
        return locale_code in self._by_id[country_id].locales

    def is_currency_enabled(self, country_id: UUID, currency_code: str) -> bool:
        """Return whether a currency is enabled.

        Args:
            country_id: Stable country identifier.
            currency_code: ISO currency code.

        Returns:
            Whether the currency is enabled.
        """
        return currency_code in self._by_id[country_id].currencies

    def geography_belongs_to_country(
        self,
        country_id: UUID,
        geography_area_id: UUID,
    ) -> bool:
        """Return whether a geography belongs to a country.

        Args:
            country_id: Stable country identifier.
            geography_area_id: Stable geography identifier.

        Returns:
            Whether the geography belongs to the country.
        """
        return geography_area_id in self._by_id[country_id].geography_ids

    def get_address_policy(self, country_id: UUID) -> AddressPolicy:
        """Return a country's address policy.

        Args:
            country_id: Stable country identifier.

        Returns:
            Registered address policy.
        """
        return self._by_id[country_id].address_policy

    def get_active_policy_version(
        self,
        country_id: UUID,
        consent_type: str,
    ) -> str:
        """Return an active consent policy version.

        Args:
            country_id: Stable country identifier.
            consent_type: Consent type code.

        Returns:
            Active policy version.
        """
        return self._by_id[country_id].policy_versions[consent_type]


class RecordingAuditWriter:
    """Record safe audit events and reject sensitive payload keys."""

    def __init__(self) -> None:
        """Initialize an empty audit record list."""
        self.records: list[tuple[AuditEventInput, DatabaseTransaction]] = []

    def stage(
        self,
        event: AuditEventInput,
        transaction: DatabaseTransaction,
    ) -> None:
        """Record a safe event with its transaction.

        Args:
            event: Redacted audit event.
            transaction: Originating database transaction.

        Raises:
            ValueError: If the event contains a sensitive payload key.
        """
        if _contains_sensitive_key(event.before_data) or _contains_sensitive_key(
            event.after_data,
        ):
            raise ValueError("Audit event contains a sensitive payload key.")
        self.records.append((event, transaction))


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if str(key).casefold() in SENSITIVE_AUDIT_KEYS:
                return True
            if _contains_sensitive_key(nested_value):
                return True
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False
