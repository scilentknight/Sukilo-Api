"""Test customer address ownership and country non-disclosure."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import Address
from app.modules.customers.service import (
    AddressNotFoundError,
    AddressService,
    CreateAddress,
    UpdateAddress,
)
from app.modules.customers.types import AddressType, DefaultKind
from app.ports.country import AddressPolicy, CountryReference

NOW = datetime(2026, 10, 12, tzinfo=UTC)
OWNER_ID = UUID(int=1)
ATTACKER_ID = UUID(int=2)
NP_ID = UUID(int=3)
AE_ID = UUID(int=4)
AREA_ID = UUID(int=5)


def _actor(user_id: UUID) -> AuthContext:
    """Build one authenticated principal."""
    return AuthContext(
        user_id=user_id,
        session_id=UUID(int=10),
        auth_version=1,
        authentication_methods=("pwd",),
        authenticated_at=NOW,
        correlation_id=UUID(int=11),
    )


class Countries:
    """Resolve two trusted countries."""

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve Nepal or UAE."""
        return {
            "NP": CountryReference(NP_ID, "NP", "ACTIVE"),
            "AE": CountryReference(AE_ID, "AE", "ACTIVE"),
        }[code]

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Enable customer addresses."""
        del country_id
        return operation == "CUSTOMER_ADDRESS"

    def geography_belongs_to_country(
        self,
        country_id: UUID,
        geography_area_id: UUID,
    ) -> bool:
        """Accept the fixture geography only in Nepal."""
        return country_id == NP_ID and geography_area_id == AREA_ID

    def get_address_policy(self, country_id: UUID) -> AddressPolicy:
        """Return optional address policy."""
        del country_id
        return AddressPolicy(False, False, True)


class Repository:
    """Store rows while exposing only scoped methods."""

    def __init__(self) -> None:
        """Initialize empty storage."""
        self.rows: list[Address] = []

    def list_addresses(self, user_id: UUID, country_id: UUID) -> list[Address]:
        """Return active scoped rows."""
        return [
            row
            for row in self.rows
            if row.user_id == user_id
            and row.country_id == country_id
            and row.deleted_at is None
        ]

    def get_address(
        self,
        user_id: UUID,
        country_id: UUID,
        address_id: UUID,
        *,
        for_update: bool = False,
    ) -> Address | None:
        """Return one active scoped row."""
        del for_update
        return next(
            (
                row
                for row in self.list_addresses(user_id, country_id)
                if row.address_id == address_id
            ),
            None,
        )

    def add_address(self, address: Address) -> None:
        """Add one row."""
        self.rows.append(address)

    def lock_default_scope(
        self,
        user_id: UUID,
        country_id: UUID,
        kind: DefaultKind,
    ) -> None:
        """Accept one logical lock."""
        del user_id, country_id, kind

    def lock_active_addresses(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[Address]:
        """Return active scoped rows."""
        return self.list_addresses(user_id, country_id)

    def flush(self) -> None:
        """Flush without committing."""


class Audit:
    """Discard audit events."""

    def stage(self, event: object, transaction: DatabaseTransaction) -> None:
        """Discard one staged event."""
        del event, transaction


def _service() -> AddressService:
    """Build an ownership-test service."""
    return AddressService(
        repository=Repository(),
        countries=Countries(),
        audit_writer=Audit(),
        transaction=DatabaseTransaction(object()),
        clock=lambda: NOW,
    )


def _command(actor: AuthContext) -> CreateAddress:
    """Build one valid owner address."""
    return CreateAddress(
        actor=actor,
        country_code="NP",
        geography_area_id=AREA_ID,
        recipient_name="Private Recipient",
        phone="+9779841234567",
        address_line_1="Private Line",
        address_line_2=None,
        landmark=None,
        city_text="Kathmandu",
        postal_code=None,
        latitude=Decimal("27.7"),
        longitude=Decimal("85.3"),
        address_type=AddressType.HOME,
        is_shipping=True,
        is_billing=False,
        is_default_shipping=False,
        is_default_billing=False,
    )


def _update(created: object, actor: AuthContext) -> UpdateAddress:
    """Build an attempted full replacement."""
    command = _command(actor)
    return UpdateAddress(
        actor=actor,
        country_code="NP",
        geography_area_id=command.geography_area_id,
        recipient_name=command.recipient_name,
        phone=command.phone,
        address_line_1=command.address_line_1,
        address_line_2=command.address_line_2,
        landmark=command.landmark,
        city_text=command.city_text,
        postal_code=command.postal_code,
        latitude=command.latitude,
        longitude=command.longitude,
        address_type=command.address_type,
        is_shipping=command.is_shipping,
        is_billing=command.is_billing,
        is_default_shipping=command.is_default_shipping,
        is_default_billing=command.is_default_billing,
        address_id=created.address_id,
        expected_version=created.version,
    )


@pytest.mark.parametrize("operation", ["get", "update", "delete", "default"])
def test_cross_owner_mutations_return_identical_not_found(operation: str) -> None:
    """Hide existence from every cross-owner address operation."""
    service = _service()
    created = service.create_address(_command(_actor(OWNER_ID)))
    attacker = _actor(ATTACKER_ID)
    with pytest.raises(AddressNotFoundError, match="Address is unavailable."):
        if operation == "get":
            service.get_address(attacker, "NP", created.address_id)
        elif operation == "update":
            service.update_address(_update(created, attacker))
        elif operation == "delete":
            service.delete_address(
                attacker,
                "NP",
                created.address_id,
                created.version,
            )
        else:
            service.set_default(
                attacker,
                "NP",
                created.address_id,
                DefaultKind.SHIPPING,
            )


def test_owned_address_with_wrong_country_returns_not_found() -> None:
    """Hide an owned row when the resolved country differs."""
    service = _service()
    owner = _actor(OWNER_ID)
    created = service.create_address(_command(owner))
    with pytest.raises(AddressNotFoundError, match="Address is unavailable."):
        service.get_address(owner, "AE", created.address_id)


def test_list_addresses_never_crosses_owner_or_country_scope() -> None:
    """Return only active rows for the authenticated owner and country."""
    service = _service()
    owner = _actor(OWNER_ID)
    created = service.create_address(_command(owner))
    attacker = _actor(ATTACKER_ID)
    assert service.list_addresses(attacker, "NP") == []
    assert len(service.list_addresses(owner, "NP")) == 1
    assert service.list_addresses(owner, "NP")[0].address_id == created.address_id
    assert service.list_addresses(owner, "AE") == []
