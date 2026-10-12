"""Define narrow customer persistence contracts."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.customers.models import Address, ConsentRecord, UserPreference
from app.modules.customers.types import DefaultKind


class PreferenceRepositoryPort(Protocol):
    """Describe preference persistence used by customer services."""

    def get(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserPreference | None:
        """Return one account preference row."""
        ...

    def add(self, preference: UserPreference) -> None:
        """Stage one account preference row."""
        ...

    def flush(self) -> None:
        """Flush staged preference state."""
        ...


class ConsentRepositoryPort(Protocol):
    """Describe append-only consent and marketing projection persistence."""

    def add_consent(self, consent: ConsentRecord) -> None:
        """Stage one immutable consent record."""
        ...

    def get_preference(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserPreference | None:
        """Return one account preference projection."""
        ...

    def add_preference(self, preference: UserPreference) -> None:
        """Stage one preference projection."""
        ...

    def list_current_consents(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[ConsentRecord]:
        """Return deterministic current consent state."""
        ...

    def list_consent_history(
        self,
        user_id: UUID,
        country_id: UUID,
        *,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ConsentRecord]:
        """Return a bounded consent history page."""
        ...

    def flush(self) -> None:
        """Flush staged consent and projection state."""
        ...


class AddressRepositoryPort(Protocol):
    """Describe scoped customer address persistence."""

    def list_addresses(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[Address]:
        """Return active addresses for one owner and country."""
        ...

    def get_address(
        self,
        user_id: UUID,
        country_id: UUID,
        address_id: UUID,
        *,
        for_update: bool = False,
    ) -> Address | None:
        """Return one active exactly scoped address."""
        ...

    def add_address(self, address: Address) -> None:
        """Stage one customer address."""
        ...

    def lock_default_scope(
        self,
        user_id: UUID,
        country_id: UUID,
        kind: DefaultKind,
    ) -> None:
        """Acquire a transaction-scoped default advisory lock."""
        ...

    def lock_active_addresses(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[Address]:
        """Lock and return all active scoped addresses."""
        ...

    def flush(self) -> None:
        """Flush staged address state."""
        ...


Clock = Callable[[], datetime]
