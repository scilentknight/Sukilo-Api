"""Define the identity subsystem's narrow country contract."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CountryReference:
    """Describe a country without exposing market persistence."""

    country_id: UUID
    code: str
    status: str


@dataclass(frozen=True, slots=True)
class AddressPolicy:
    """Describe validated country-specific address requirements."""

    postal_code_required: bool
    geography_required: bool
    coordinates_allowed: bool


class CountryReader(Protocol):
    """Read country policy through a stable domain boundary."""

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve a country code."""
        ...

    def resolve_country_id(self, country_id: UUID) -> CountryReference:
        """Resolve a stable country identifier."""
        ...

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Return whether a country operation is enabled."""
        ...

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Return whether a locale is enabled in a country."""
        ...

    def is_currency_enabled(self, country_id: UUID, currency_code: str) -> bool:
        """Return whether a currency is enabled in a country."""
        ...

    def default_currency_code(self, country_id: UUID) -> str:
        """Return the settlement currency for a country."""
        ...

    def default_locale_code(self, country_id: UUID) -> str:
        """Return the default storefront locale for a country."""
        ...

    def geography_belongs_to_country(
        self,
        country_id: UUID,
        geography_area_id: UUID,
    ) -> bool:
        """Return whether a geography belongs to a country."""
        ...

    def get_address_policy(self, country_id: UUID) -> AddressPolicy:
        """Return validated address policy for a country."""
        ...

    def get_active_policy_version(
        self,
        country_id: UUID,
        consent_type: str,
    ) -> str:
        """Return the active consent policy version."""
        ...
