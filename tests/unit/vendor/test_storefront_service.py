"""Test storefront go-live rules and payout redaction."""

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import (
    Store,
    StoreTranslation,
    Vendor,
    VendorCountry,
    VendorPayoutAccount,
    Warehouse,
)
from app.modules.vendor.schemas import StoreUpsertRequest
from app.modules.vendor.storefront import VendorStorefrontService
from app.modules.vendor.types import (
    AccountType,
    OnboardingStatus,
    StorefrontStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)
from app.ports.audit import AuditEventInput
from app.ports.country import CountryReference

NOW = datetime(2026, 10, 28, tzinfo=UTC)
OWNER = UUID(int=1)
REVIEWER = UUID(int=2)
NEPAL = UUID(int=10)
UAE = UUID(int=20)
RAW_ACCOUNT = "998877665544"


class Countries:
    """Resolve Nepal and UAE with Nepal settlement defaults."""

    def resolve_country(self, code: str) -> CountryReference:
        """Return a fixed country reference."""
        normalized = code.upper()
        country_id = NEPAL if normalized == "NP" else UAE
        return CountryReference(country_id, normalized, "ACTIVE")

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Allow storefront locales in the unit tests."""
        del country_id, locale_code
        return True

    def is_currency_enabled(self, country_id: UUID, currency_code: str) -> bool:
        """Allow NPR only."""
        del country_id
        return currency_code == "NPR"

    def default_currency_code(self, country_id: UUID) -> str:
        """Return NPR for Nepal."""
        del country_id
        return "NPR"

    def default_locale_code(self, country_id: UUID) -> str:
        """Return the Nepal locale."""
        del country_id
        return "ne"


class Cipher:
    """Prefix account numbers so tests can see ciphertext."""

    def encrypt(self, account_number: str) -> str:
        """Return deterministic ciphertext."""
        return f"enc:{account_number}"


class Audit:
    """Record staged audit events."""

    def __init__(self) -> None:
        """Initialize an empty event list."""
        self.events: list[AuditEventInput] = []

    def stage(self, event: AuditEventInput, transaction: DatabaseTransaction) -> None:
        """Record one event."""
        del transaction
        self.events.append(event)


class Authorizer:
    """Allow owner actions in Nepal and payout verification for the reviewer."""

    def __init__(self, *, allow_owner_verify: bool = False) -> None:
        """Initialize the permission switch."""
        self.allow_owner_verify = allow_owner_verify

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Allow Nepal owner writes and reviewer verification."""
        del vendor_id
        if country_id != NEPAL:
            return False
        if permission == "vendor.payout.verify":
            return actor.user_id == REVIEWER or self.allow_owner_verify
        return actor.user_id == OWNER and permission in {
            "vendor.store.write",
            "vendor.warehouse.write",
        }


class Repository:
    """Store one organization's shop rows in memory."""

    def __init__(self, status: OnboardingStatus = OnboardingStatus.APPROVED) -> None:
        """Initialize an approved Nepal application and an empty UAE application."""
        self.vendor = Vendor(
            vendor_id=uuid4(),
            legal_name="Acme Trading",
            display_name="Acme",
            vendor_type=VendorType.COMPANY,
            registration_country_id=NEPAL,
            status=VendorStatus.ACTIVE,
            created_by_user_id=OWNER,
            version=1,
        )
        self.countries = {
            NEPAL: _country(self.vendor.vendor_id, NEPAL, status),
            UAE: _country(self.vendor.vendor_id, UAE, OnboardingStatus.APPROVED),
        }
        self.stores: list[Store] = []
        self.translations: list[StoreTranslation] = []
        self.payouts: list[VendorPayoutAccount] = []
        self.warehouses: list[Warehouse] = []

    def get_vendor(self, vendor_id: UUID, *, for_update: bool = False) -> Vendor | None:
        """Return the stored organization."""
        del for_update
        if vendor_id != self.vendor.vendor_id:
            return None
        return self.vendor

    def get_country(self, vendor_country_id: UUID, *, for_update: bool = False) -> VendorCountry | None:
        """Return one country row by identifier."""
        del for_update
        return next(
            (row for row in self.countries.values() if row.vendor_country_id == vendor_country_id),
            None,
        )

    def get_country_for_market(self, vendor_id: UUID, country_id: UUID) -> VendorCountry | None:
        """Return one organization's country row."""
        if vendor_id != self.vendor.vendor_id:
            return None
        return self.countries.get(country_id)

    def get_store_for_vendor_country(
        self,
        vendor_id: UUID,
        country_id: UUID,
        *,
        for_update: bool = False,
    ) -> Store | None:
        """Return the store for one country."""
        del for_update
        return next(
            (row for row in self.stores if row.vendor_id == vendor_id and row.country_id == country_id),
            None,
        )

    def find_store_by_country_slug(self, country_id: UUID, slug: str) -> Store | None:
        """Return a store that already uses the slug."""
        return next(
            (row for row in self.stores if row.country_id == country_id and row.slug == slug),
            None,
        )

    def add_store(self, store: Store) -> None:
        """Record a store."""
        self.stores.append(store)

    def get_translation(self, store_id: UUID, locale_code: str) -> StoreTranslation | None:
        """Return one translation."""
        return next(
            (
                row
                for row in self.translations
                if row.store_id == store_id and row.locale_code == locale_code
            ),
            None,
        )

    def add_translation(self, translation: StoreTranslation) -> None:
        """Record a translation."""
        self.translations.append(translation)

    def add_payout(self, account: VendorPayoutAccount) -> None:
        """Record a payout destination."""
        self.payouts.append(account)

    def get_payout(self, payout_account_id: UUID, *, for_update: bool = False) -> VendorPayoutAccount | None:
        """Return one payout destination."""
        del for_update
        return next(
            (row for row in self.payouts if row.vendor_payout_account_id == payout_account_id),
            None,
        )

    def get_default_payout(
        self,
        vendor_country_id: UUID,
        currency_code: str,
        *,
        for_update: bool = False,
    ) -> VendorPayoutAccount | None:
        """Return the default payout for one currency."""
        del for_update
        return next(
            (
                row
                for row in self.payouts
                if row.vendor_country_id == vendor_country_id
                and row.currency_code == currency_code
                and row.is_default
            ),
            None,
        )

    def clear_default_payouts(self, vendor_country_id: UUID, currency_code: str) -> None:
        """Clear previous default payout rows."""
        for row in self.payouts:
            if row.vendor_country_id == vendor_country_id and row.currency_code == currency_code:
                row.is_default = False

    def add_warehouse(self, warehouse: Warehouse) -> None:
        """Record a warehouse."""
        self.warehouses.append(warehouse)

    def find_warehouse_by_code(self, vendor_id: UUID, code: str) -> Warehouse | None:
        """Return a warehouse with the same code in any country."""
        return next(
            (row for row in self.warehouses if row.vendor_id == vendor_id and row.code == code),
            None,
        )

    def list_warehouses(
        self,
        vendor_id: UUID,
        country_id: UUID,
        *,
        for_update: bool = False,
    ) -> list[Warehouse]:
        """Return warehouses in one country."""
        del for_update
        return [
            row
            for row in self.warehouses
            if row.vendor_id == vendor_id and row.country_id == country_id
        ]


def _country(vendor_id: UUID, country_id: UUID, status: OnboardingStatus) -> VendorCountry:
    """Build one country application."""
    return VendorCountry(
        vendor_country_id=uuid4(),
        vendor_id=vendor_id,
        country_id=country_id,
        onboarding_status=status,
        verification_status=VerificationStatus.PENDING,
        selling_enabled=False,
        payout_enabled=False,
        version=1,
    )


def _actor(user_id: UUID) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())


def _service(repository: Repository, authorizer: Authorizer | None = None) -> tuple[VendorStorefrontService, Audit]:
    """Build a storefront service and its audit recorder."""
    audit = Audit()
    service = VendorStorefrontService(
        repository,
        Countries(),
        authorizer or Authorizer(),
        Cipher(),
        audit,
        DatabaseTransaction(object()),
        lambda: NOW,
    )
    return service, audit


def test_draft_application_cannot_create_store() -> None:
    """Reject a store before the country application is approved."""
    repository = Repository(OnboardingStatus.DRAFT)
    service, _audit = _service(repository)
    with pytest.raises(VendorServiceError) as captured:
        service.save_store(
            _actor(OWNER),
            vendor_id=repository.vendor.vendor_id,
            country_code="NP",
            slug="Acme Store",
            locale_code="ne",
            name="Acme",
            description=None,
            seo_title=None,
            seo_description=None,
        )
    assert captured.value.status_code == 409
    assert captured.value.code == "APPLICATION_NOT_APPROVED"
    assert repository.stores == []


def test_nepal_owner_cannot_create_uae_warehouse() -> None:
    """Keep a Nepal owner out of a UAE warehouse."""
    repository = Repository()
    service, _audit = _service(repository)
    with pytest.raises(VendorServiceError) as captured:
        service.add_warehouse(
            _actor(OWNER),
            vendor_id=repository.vendor.vendor_id,
            country_code="AE",
            code="DXB-1",
            address_line_1="1 Market",
            city_text="Dubai",
            postal_code=None,
            contact_name="Asha",
            contact_phone="9800000000",
            fulfillment_enabled=True,
            pickup_enabled=False,
            status=StorefrontStatus.ACTIVE,
        )
    assert captured.value.status_code == 403
    assert repository.warehouses == []


def test_payout_response_and_audit_omit_the_account_secret(caplog: pytest.LogCaptureFixture) -> None:
    """Return only the mask and keep the secret out of audit and logs."""
    repository = Repository()
    service, audit = _service(repository)
    with caplog.at_level(logging.DEBUG):
        view = service.save_payout(
            _actor(OWNER),
            vendor_id=repository.vendor.vendor_id,
            country_code="NP",
            account_type=AccountType.BANK,
            currency_code="npr",
            account_holder_name="Acme Trading",
            account_number=RAW_ACCOUNT,
            bank_name="Nepal Bank",
            is_default=True,
        )
    stored = repository.payouts[0]
    payload = asdict(view)
    assert view.account_number_masked == "****5544"
    assert "account_number_encrypted" not in payload
    assert RAW_ACCOUNT not in str(payload)
    assert stored.account_number_encrypted == f"enc:{RAW_ACCOUNT}"
    assert RAW_ACCOUNT not in repr(stored)
    assert stored.account_number_encrypted not in repr(stored)
    assert RAW_ACCOUNT not in str(audit.events)
    assert stored.account_number_encrypted not in str(audit.events)
    assert RAW_ACCOUNT not in caplog.text
    assert stored.account_number_encrypted not in caplog.text


def test_replacing_the_default_payout_clears_the_previous_default() -> None:
    """Keep one default payout for a currency."""
    repository = Repository()
    service, _audit = _service(repository)
    first = _save_payout(service, repository)
    second = _save_payout(service, repository)
    assert repository.payouts[0].vendor_payout_account_id == first.vendor_payout_account_id
    assert repository.payouts[0].is_default is False
    assert second.is_default is True


def test_activation_fails_while_payout_is_pending() -> None:
    """Leave selling disabled when payout verification is still pending."""
    repository = Repository()
    service, _audit = _service(repository)
    _ready_shop(service, repository, verify=False)
    with pytest.raises(VendorServiceError) as captured:
        service.activate(_actor(OWNER), vendor_id=repository.vendor.vendor_id, country_code="NP")
    assert captured.value.code == "PAYOUT_NOT_VERIFIED"
    assert repository.countries[NEPAL].selling_enabled is False
    assert repository.stores[0].status is StorefrontStatus.DRAFT


def test_activation_fails_without_a_fulfillment_warehouse() -> None:
    """Leave selling disabled when no warehouse can fulfill."""
    repository = Repository()
    service, _audit = _service(repository)
    _save_store(service, repository)
    _save_payout(service, repository)
    _verify(service, repository)
    service.add_warehouse(
        _actor(OWNER),
        vendor_id=repository.vendor.vendor_id,
        country_code="NP",
        code="KTM-1",
        address_line_1="1 Market",
        city_text="Kathmandu",
        postal_code="44600",
        contact_name="Asha",
        contact_phone="9800000000",
        fulfillment_enabled=False,
        pickup_enabled=True,
        status=StorefrontStatus.ACTIVE,
    )
    with pytest.raises(VendorServiceError) as captured:
        service.activate(_actor(OWNER), vendor_id=repository.vendor.vendor_id, country_code="NP")
    assert captured.value.code == "WAREHOUSE_NOT_READY"
    assert repository.countries[NEPAL].selling_enabled is False


def test_activation_enables_selling_only_after_every_check() -> None:
    """Enable selling for the approved country when every check passes."""
    repository = Repository()
    service, audit = _service(repository)
    _ready_shop(service, repository, verify=True)
    view = service.activate(_actor(OWNER), vendor_id=repository.vendor.vendor_id, country_code="NP")
    assert view.selling_enabled is True
    assert view.payout_enabled is True
    assert view.status is StorefrontStatus.ACTIVE
    assert repository.countries[UAE].selling_enabled is False
    assert repository.stores[0].rating == Decimal("0")
    assert audit.events[-1].action == "vendor.store.activated"
    assert "account_number" not in str(audit.events[-1])


def test_failed_activation_leaves_selling_disabled() -> None:
    """Keep the previous selling flag when a later check fails."""
    repository = Repository()
    service, _audit = _service(repository)
    _save_payout(service, repository)
    _verify(service, repository)
    _save_store(service, repository, locale_code="en")
    service.add_warehouse(
        _actor(OWNER),
        vendor_id=repository.vendor.vendor_id,
        country_code="NP",
        code="KTM-1",
        address_line_1="1 Market",
        city_text="Kathmandu",
        postal_code="44600",
        contact_name="Asha",
        contact_phone="9800000000",
        fulfillment_enabled=True,
        pickup_enabled=False,
        status=StorefrontStatus.ACTIVE,
    )
    with pytest.raises(VendorServiceError) as captured:
        service.activate(_actor(OWNER), vendor_id=repository.vendor.vendor_id, country_code="NP")
    assert captured.value.code == "TRANSLATION_MISSING"
    assert repository.countries[NEPAL].selling_enabled is False
    assert repository.stores[0].status is StorefrontStatus.DRAFT


def test_two_stores_in_one_country_cannot_share_a_slug() -> None:
    """Reject a second store that reuses a country slug."""
    repository = Repository()
    service, _audit = _service(repository)
    repository.stores.append(
        Store(
            store_id=uuid4(),
            vendor_id=uuid4(),
            country_id=NEPAL,
            slug="acme-store",
            status=StorefrontStatus.DRAFT,
            rating=Decimal("0"),
            rating_count=0,
            followers_count=0,
            version=1,
        )
    )
    with pytest.raises(VendorServiceError) as captured:
        _save_store(service, repository, slug="Acme Store")
    assert captured.value.code == "SLUG_TAKEN"


def test_warehouse_code_is_unique_across_the_vendor() -> None:
    """Reject a warehouse code already used in another country."""
    repository = Repository()
    service, _audit = _service(repository)
    service.add_warehouse(
        _actor(OWNER),
        vendor_id=repository.vendor.vendor_id,
        country_code="NP",
        code="hub-1",
        address_line_1="1 Market",
        city_text="Kathmandu",
        postal_code=None,
        contact_name="Asha",
        contact_phone="9800000000",
        fulfillment_enabled=True,
        pickup_enabled=False,
        status=StorefrontStatus.ACTIVE,
    )
    repository.countries[UAE] = _country(repository.vendor.vendor_id, UAE, OnboardingStatus.APPROVED)
    with pytest.raises(VendorServiceError) as captured:
        service.add_warehouse(
            _actor(OWNER),
            vendor_id=repository.vendor.vendor_id,
            country_code="NP",
            code="HUB-1",
            address_line_1="2 Market",
            city_text="Lalitpur",
            postal_code=None,
            contact_name="Asha",
            contact_phone="9800000001",
            fulfillment_enabled=False,
            pickup_enabled=False,
            status=StorefrontStatus.DRAFT,
        )
    assert captured.value.code == "WAREHOUSE_CODE_TAKEN"


def test_owner_cannot_verify_own_payout_account() -> None:
    """Reject payout verification by the organization creator."""
    repository = Repository()
    service, _audit = _service(repository, Authorizer(allow_owner_verify=True))
    view = _save_payout(service, repository)
    with pytest.raises(VendorServiceError) as captured:
        service.verify_payout(_actor(OWNER), view.vendor_payout_account_id)
    assert captured.value.status_code == 403
    assert repository.payouts[0].verification_status is VerificationStatus.PENDING


def test_store_schema_rejects_rating_fields() -> None:
    """Reject rating fields on the store update schema."""
    with pytest.raises(ValidationError):
        StoreUpsertRequest.model_validate(
            {
                "vendor_id": str(uuid4()),
                "country_code": "NP",
                "slug": "acme",
                "locale_code": "ne",
                "name": "Acme",
                "rating": 5,
            }
        )


def test_warehouse_model_has_no_shopper_address() -> None:
    """Keep warehouse addresses on the warehouse table."""
    assert "address_id" not in Warehouse.__table__.columns


def _save_store(
    service: VendorStorefrontService,
    repository: Repository,
    *,
    slug: str = "Acme Store",
    locale_code: str = "ne",
) -> None:
    """Save one store translation."""
    service.save_store(
        _actor(OWNER),
        vendor_id=repository.vendor.vendor_id,
        country_code="NP",
        slug=slug,
        locale_code=locale_code,
        name="Acme",
        description=None,
        seo_title=None,
        seo_description=None,
    )


def _save_payout(service: VendorStorefrontService, repository: Repository):
    """Save one default Nepal payout destination."""
    return service.save_payout(
        _actor(OWNER),
        vendor_id=repository.vendor.vendor_id,
        country_code="NP",
        account_type=AccountType.BANK,
        currency_code="NPR",
        account_holder_name="Acme Trading",
        account_number=RAW_ACCOUNT,
        bank_name=None,
        is_default=True,
    )


def _verify(service: VendorStorefrontService, repository: Repository) -> None:
    """Mark the stored payout verified through the reviewer."""
    service.verify_payout(_actor(REVIEWER), repository.payouts[-1].vendor_payout_account_id)


def _ready_shop(service: VendorStorefrontService, repository: Repository, *, verify: bool) -> None:
    """Save the shop pieces used by activation tests."""
    _save_store(service, repository)
    _save_payout(service, repository)
    if verify:
        _verify(service, repository)
    service.add_warehouse(
        _actor(OWNER),
        vendor_id=repository.vendor.vendor_id,
        country_code="NP",
        code="KTM-1",
        address_line_1="1 Market",
        city_text="Kathmandu",
        postal_code="44600",
        contact_name="Asha",
        contact_phone="9800000000",
        fulfillment_enabled=True,
        pickup_enabled=False,
        status=StorefrontStatus.ACTIVE,
    )
