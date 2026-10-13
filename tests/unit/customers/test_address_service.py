"""Test customer address service invariants."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import Address
from app.modules.customers.service import (
    AddressConflictError,
    AddressNotFoundError,
    AddressService,
    AddressValidationError,
    CreateAddress,
    UpdateAddress,
)
from app.modules.customers.types import AddressType, DefaultKind
from app.ports.audit import AuditEventInput
from app.ports.country import AddressPolicy, CountryReference

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
OTHER_USER_ID = UUID(int=2)
COUNTRY_ID = UUID(int=3)
OTHER_COUNTRY_ID = UUID(int=4)
GEOGRAPHY_ID = UUID(int=5)
ADDRESS_ID = UUID(int=6)


def _actor(user_id: UUID = USER_ID) -> AuthContext:
    """Build one authenticated customer."""
    return AuthContext(
        user_id=user_id,
        session_id=UUID(int=20),
        auth_version=1,
        authentication_methods=("pwd",),
        authenticated_at=NOW,
        correlation_id=UUID(int=21),
    )


class CountriesFake:
    """Provide deterministic address country policy."""

    def __init__(self, policy: AddressPolicy | None = None) -> None:
        """Initialize address policy."""
        self.policy = policy or AddressPolicy(True, True, True)

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve supported country codes."""
        references = {
            "NP": CountryReference(COUNTRY_ID, "NP", "ACTIVE"),
            "AE": CountryReference(OTHER_COUNTRY_ID, "AE", "ACTIVE"),
        }
        return references[code]

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Enable only the customer address operation."""
        return operation == "CUSTOMER_ADDRESS"

    def geography_belongs_to_country(
        self,
        country_id: UUID,
        geography_area_id: UUID,
    ) -> bool:
        """Accept one geography in Nepal."""
        return country_id == COUNTRY_ID and geography_area_id == GEOGRAPHY_ID

    def get_address_policy(self, country_id: UUID) -> AddressPolicy:
        """Return configured policy."""
        return self.policy


class AddressRepositoryFake:
    """Persist address rows in memory with scoped lookups."""

    def __init__(self) -> None:
        """Initialize empty address persistence."""
        self.rows: list[Address] = []
        self.locked_kinds: list[DefaultKind] = []
        self.active_lock_count = 0
        self.flush_count = 0
        self.shipping_default_snapshots: list[tuple[UUID, ...]] = []

    def list_addresses(self, user_id: UUID, country_id: UUID) -> list[Address]:
        """Return active scoped rows newest first."""
        rows = [
            row
            for row in self.rows
            if row.user_id == user_id
            and row.country_id == country_id
            and row.deleted_at is None
        ]
        return sorted(rows, key=lambda row: (row.created_at, row.address_id), reverse=True)

    def get_address(
        self,
        user_id: UUID,
        country_id: UUID,
        address_id: UUID,
        *,
        for_update: bool = False,
    ) -> Address | None:
        """Return one active exactly scoped row."""
        del for_update
        return next(
            (
                row
                for row in self.rows
                if row.user_id == user_id
                and row.country_id == country_id
                and row.address_id == address_id
                and row.deleted_at is None
            ),
            None,
        )

    def add_address(self, address: Address) -> None:
        """Stage a new address."""
        self.rows.append(address)

    def lock_default_scope(
        self,
        user_id: UUID,
        country_id: UUID,
        kind: DefaultKind,
    ) -> None:
        """Record one advisory lock."""
        del user_id, country_id
        self.locked_kinds.append(kind)

    def lock_active_addresses(
        self,
        user_id: UUID,
        country_id: UUID,
    ) -> list[Address]:
        """Lock and return active scoped rows."""
        self.active_lock_count += 1
        return self.list_addresses(user_id, country_id)

    def flush(self) -> None:
        """Record a flush without committing."""
        self.flush_count += 1
        self.shipping_default_snapshots.append(
            tuple(row.address_id for row in self.rows if row.is_default_shipping)
        )


class AuditFake:
    """Capture transaction-bound audit events."""

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
    repository: AddressRepositoryFake,
    policy: AddressPolicy | None = None,
) -> tuple[AddressService, AuditFake]:
    """Build a deterministic address service."""
    audit = AuditFake()
    service = AddressService(
        repository=repository,
        countries=CountriesFake(policy),
        audit_writer=audit,
        transaction=DatabaseTransaction(object()),
        clock=lambda: NOW,
    )
    return service, audit


def _create(**overrides: object) -> CreateAddress:
    """Build a valid Nepal address command."""
    values: dict[str, object] = {
        "actor": _actor(),
        "country_code": "NP",
        "geography_area_id": GEOGRAPHY_ID,
        "recipient_name": "Customer",
        "phone": "9841234567",
        "address_line_1": "Lazimpat",
        "address_line_2": None,
        "landmark": None,
        "city_text": "Kathmandu",
        "postal_code": "44600",
        "latitude": Decimal("27.7172453"),
        "longitude": Decimal("85.3239605"),
        "address_type": AddressType.HOME,
        "is_shipping": True,
        "is_billing": False,
        "is_default_shipping": False,
        "is_default_billing": False,
    }
    values.update(overrides)
    return CreateAddress(**values)


def _update(address_id: UUID, version: int, **overrides: object) -> UpdateAddress:
    """Build a valid full address replacement."""
    values = dict(_create().__dict__) if hasattr(_create(), "__dict__") else {
        field: getattr(_create(), field)
        for field in (
            "actor",
            "country_code",
            "geography_area_id",
            "recipient_name",
            "phone",
            "address_line_1",
            "address_line_2",
            "landmark",
            "city_text",
            "postal_code",
            "latitude",
            "longitude",
            "address_type",
            "is_shipping",
            "is_billing",
            "is_default_shipping",
            "is_default_billing",
        )
    }
    values.update({"address_id": address_id, "expected_version": version})
    values.update(overrides)
    return UpdateAddress(**values)


def test_create_normalizes_phone_and_makes_first_eligible_default() -> None:
    """Normalize delivery phone and default the first shipping address."""
    repository = AddressRepositoryFake()
    service, audit = _service(repository)
    view = service.create_address(_create())
    assert view.phone_e164 == "+9779841234567"
    assert view.is_default_shipping is True
    assert view.is_default_billing is False
    assert repository.locked_kinds == [DefaultKind.SHIPPING]
    assert repository.active_lock_count == 1
    assert audit.records[0][0].after_data == {
        "changed_fields": (
            "geography_area_id",
            "recipient_name",
            "phone_e164",
            "address_line_1",
            "city_text",
            "postal_code",
            "latitude",
            "longitude",
            "address_type",
            "is_shipping",
            "is_default_shipping",
        )
    }
    assert "Customer" not in repr(audit.records[0][0])
    assert "+9779841234567" not in repr(audit.records[0][0])


@pytest.mark.parametrize(
    "overrides",
    [
        {"address_type": "WAREHOUSE"},
        {"is_shipping": False, "is_billing": False},
        {"is_shipping": False, "is_default_shipping": True, "is_billing": True},
        {"latitude": Decimal("91")},
        {"longitude": Decimal("-181")},
        {"recipient_name": "x" * 161},
    ],
)
def test_create_rejects_invalid_shape(overrides: dict[str, object]) -> None:
    """Reject unsupported type, flags, coordinates, and excess lengths."""
    service, _ = _service(AddressRepositoryFake())
    with pytest.raises(AddressValidationError):
        service.create_address(_create(**overrides))


def test_create_applies_country_geography_postal_and_coordinate_policy() -> None:
    """Reject country-disabled geography, postal, and coordinate inputs."""
    service, _ = _service(AddressRepositoryFake())
    with pytest.raises(AddressValidationError):
        service.create_address(_create(geography_area_id=UUID(int=99)))
    no_coordinates = AddressPolicy(True, True, False)
    service, _ = _service(AddressRepositoryFake(), no_coordinates)
    with pytest.raises(AddressValidationError):
        service.create_address(_create())
    required = AddressPolicy(True, True, True)
    service, _ = _service(AddressRepositoryFake(), required)
    with pytest.raises(AddressValidationError):
        service.create_address(_create(postal_code=None))


def test_list_and_get_are_exactly_owner_and_country_scoped() -> None:
    """Hide cross-owner and cross-country addresses as not found."""
    repository = AddressRepositoryFake()
    service, _ = _service(repository)
    own = service.create_address(_create())
    service.create_address(_create(actor=_actor(OTHER_USER_ID)))
    assert [item.address_id for item in service.list_addresses(_actor(), "NP")] == [
        own.address_id
    ]
    with pytest.raises(AddressNotFoundError):
        service.get_address(_actor(OTHER_USER_ID), "NP", own.address_id)
    with pytest.raises(AddressNotFoundError):
        service.get_address(_actor(), "AE", own.address_id)


def test_update_increments_version_and_clears_ineligible_default() -> None:
    """Version updates and remove defaults when eligibility is removed."""
    repository = AddressRepositoryFake()
    service, _ = _service(repository)
    created = service.create_address(_create(is_billing=True))
    updated = service.update_address(
        _update(
            created.address_id,
            created.version,
            is_shipping=False,
            is_billing=True,
            is_default_shipping=False,
        )
    )
    assert updated.version == 2
    assert updated.is_shipping is False
    assert updated.is_default_shipping is False


def test_update_and_delete_reject_stale_versions() -> None:
    """Reject lost updates and deletes with one conflict error."""
    repository = AddressRepositoryFake()
    service, _ = _service(repository)
    created = service.create_address(_create())
    with pytest.raises(AddressConflictError):
        service.update_address(_update(created.address_id, 99))
    with pytest.raises(AddressConflictError):
        service.delete_address(_actor(), "NP", created.address_id, 99)


def test_set_default_clears_old_default_and_requires_eligibility() -> None:
    """Move one default only to an address eligible for that kind."""
    repository = AddressRepositoryFake()
    service, _ = _service(repository)
    first = service.create_address(_create())
    second = service.create_address(_create())
    selected = service.set_default(
        _actor(),
        "NP",
        second.address_id,
        DefaultKind.SHIPPING,
    )
    assert selected.is_default_shipping is True
    assert repository.shipping_default_snapshots[-2:] == [
        (),
        (second.address_id,),
    ]
    assert service.get_address(_actor(), "NP", first.address_id).is_default_shipping is False
    billing_only = service.create_address(
        _create(is_shipping=False, is_billing=True)
    )
    with pytest.raises(AddressValidationError):
        service.set_default(
            _actor(),
            "NP",
            billing_only.address_id,
            DefaultKind.SHIPPING,
        )


def test_delete_default_promotes_newest_eligible_and_soft_deletes() -> None:
    """Promote the newest eligible address after deleting a default."""
    repository = AddressRepositoryFake()
    service, audit = _service(repository)
    older = service.create_address(_create())
    newer = service.create_address(_create())
    service.set_default(_actor(), "NP", older.address_id, DefaultKind.SHIPPING)
    current = service.get_address(_actor(), "NP", older.address_id)
    service.delete_address(_actor(), "NP", older.address_id, current.version)
    assert repository.shipping_default_snapshots[-2:] == [
        (),
        (newer.address_id,),
    ]
    with pytest.raises(AddressNotFoundError):
        service.get_address(_actor(), "NP", older.address_id)
    promoted = service.get_address(_actor(), "NP", newer.address_id)
    assert promoted.is_default_shipping is True
    deleted_event = audit.records[-1][0]
    assert deleted_event.action == "iam.customer.address.deleted"
    assert deleted_event.after_data == {"changed_fields": ("deleted_at",)}
