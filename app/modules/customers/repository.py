"""Persist customer preferences and append-only consent evidence."""

from hashlib import blake2b
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.modules.customers.models import Address, ConsentRecord, UserPreference
from app.modules.customers.types import DefaultKind


class CustomerRepository:
    """Provide parameterized customer preference and consent operations."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository."""
        self.db = db

    def get(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserPreference | None:
        """Return one account preference row."""
        statement = select(UserPreference).where(UserPreference.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def add(self, preference: UserPreference) -> None:
        """Stage one account preference row."""
        self.db.add(preference)

    def add_consent(self, consent: ConsentRecord) -> None:
        """Stage one immutable consent record."""
        self.db.add(consent)

    def get_preference(
        self,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> UserPreference | None:
        """Return one preference projection."""
        return self.get(user_id, for_update=for_update)

    def add_preference(self, preference: UserPreference) -> None:
        """Stage one preference projection."""
        self.add(preference)

    def list_addresses(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[Address]:
        """Return active scoped addresses newest first."""
        statement = (
            select(Address)
            .where(
                Address.user_id == user_id,
                Address.country_id == country_id,
                Address.deleted_at.is_(None),
            )
            .order_by(Address.created_at.desc(), Address.address_id.desc())
        )
        return list(self.db.scalars(statement))

    def get_address(
        self,
        user_id: UUID,
        country_id: UUID,
        address_id: UUID,
        *,
        for_update: bool = False,
    ) -> Address | None:
        """Return one active address in an exact owner-country scope."""
        statement = select(Address).where(
            Address.user_id == user_id,
            Address.country_id == country_id,
            Address.address_id == address_id,
            Address.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def add_address(self, address: Address) -> None:
        """Stage one customer-owned address."""
        self.db.add(address)

    def lock_default_scope(
        self,
        user_id: UUID,
        country_id: UUID,
        kind: DefaultKind,
    ) -> None:
        """Acquire a stable transaction-scoped PostgreSQL advisory lock."""
        key = _default_lock_key(user_id, country_id, kind)
        statement = text(
            "SELECT pg_advisory_xact_lock(:lock_key)"
        ).bindparams(lock_key=key)
        self.db.execute(statement)

    def lock_active_addresses(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[Address]:
        """Lock all active addresses in one owner-country scope."""
        statement = (
            select(Address)
            .where(
                Address.user_id == user_id,
                Address.country_id == country_id,
                Address.deleted_at.is_(None),
            )
            .order_by(Address.created_at.desc(), Address.address_id.desc())
            .with_for_update()
        )
        return list(self.db.scalars(statement))

    def list_current_consents(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[ConsentRecord]:
        """Return latest consent per type with deterministic tie-breaking."""
        statement = (
            select(ConsentRecord)
            .where(
                ConsentRecord.user_id == user_id,
                ConsentRecord.country_id == country_id,
            )
            .ext(
                distinct_on(
                    ConsentRecord.user_id,
                    ConsentRecord.country_id,
                    ConsentRecord.consent_type,
                )
            )
            .order_by(
                ConsentRecord.user_id,
                ConsentRecord.country_id,
                ConsentRecord.consent_type,
                ConsentRecord.recorded_at.desc(),
                ConsentRecord.consent_id.desc(),
            )
        )
        return list(self.db.scalars(statement))

    def list_consent_history(
        self,
        user_id: UUID,
        country_id: UUID,
        *,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ConsentRecord]:
        """Return a bounded newest-first consent evidence page."""
        if not 1 <= limit <= 101:
            raise ValueError("Consent history page limit is invalid.")
        statement = select(ConsentRecord).where(
            ConsentRecord.user_id == user_id,
            ConsentRecord.country_id == country_id,
        )
        if cursor is not None:
            statement = statement.where(
                tuple_(
                    ConsentRecord.recorded_at,
                    ConsentRecord.consent_id,
                )
                < cursor
            )
        statement = statement.order_by(
            ConsentRecord.recorded_at.desc(),
            ConsentRecord.consent_id.desc(),
        ).limit(limit)
        return list(self.db.scalars(statement))

    def flush(self) -> None:
        """Flush staged customer state without committing."""
        self.db.flush()


def _default_lock_key(
    user_id: UUID,
    country_id: UUID,
    kind: DefaultKind,
) -> int:
    payload = f"{user_id}:{country_id}:{kind.value}".encode()
    digest = blake2b(payload, digest_size=8, person=b"sukiloaddr").digest()
    return int.from_bytes(digest, byteorder="big", signed=True)
