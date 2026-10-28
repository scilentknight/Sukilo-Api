"""Configure an approved seller's country shop."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.access.vendor_grants import VendorAuthorizer
from app.modules.vendor.account_cipher import AccountNumberCipher
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import (
    Store,
    StoreTranslation,
    VendorCountry,
    VendorPayoutAccount,
    Warehouse,
)
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import (
    AccountType,
    OnboardingStatus,
    StorefrontStatus,
    VerificationStatus,
)
from app.ports.audit import AuditEventInput, AuditWriter
from app.ports.country import CountryReader


@dataclass(frozen=True, slots=True)
class StoreView:
    """Expose store copy without rating counters."""

    store_id: UUID
    vendor_id: UUID
    country_id: UUID
    slug: str
    status: StorefrontStatus
    locale_code: str
    name: str
    selling_enabled: bool
    payout_enabled: bool


@dataclass(frozen=True, slots=True)
class PayoutView:
    """Expose a payout destination without the account secret."""

    vendor_payout_account_id: UUID
    currency_code: str
    account_number_masked: str
    is_default: bool
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class WarehouseView:
    """Expose one seller warehouse."""

    warehouse_id: UUID
    vendor_id: UUID
    country_id: UUID
    code: str
    status: StorefrontStatus
    fulfillment_enabled: bool


class VendorStorefrontService:
    """Save payout, store copy, and warehouses, then run go-live checks."""

    def __init__(
        self,
        repository: VendorRepository,
        countries: CountryReader,
        authorizer: VendorAuthorizer,
        cipher: AccountNumberCipher,
        audit: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize storefront dependencies."""
        self.repository = repository
        self.countries = countries
        self.authorizer = authorizer
        self.cipher = cipher
        self.audit = audit
        self.transaction = transaction
        self.clock = clock

    def save_store(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_code: str,
        slug: str,
        locale_code: str,
        name: str,
        description: str | None,
        seo_title: str | None,
        seo_description: str | None,
    ) -> StoreView:
        """Create or update the draft store and one translation."""
        country_id = _country_id(self.countries, country_code)
        self._require(actor, "vendor.store.write", country_id, vendor_id)
        country = self._approved_country(vendor_id, country_id)
        normalized = normalize_slug(slug)
        self._require_locale(country_id, locale_code)
        cleaned_name = _required_text(name, "Store name is empty.")
        self._require_available_slug(country_id, normalized, vendor_id)
        store = self.repository.get_store_for_vendor_country(vendor_id, country_id)
        if store is None:
            store = Store(
                store_id=uuid4(),
                vendor_id=vendor_id,
                country_id=country_id,
                slug=normalized,
                status=StorefrontStatus.DRAFT,
                rating=Decimal("0"),
                rating_count=0,
                followers_count=0,
                version=1,
            )
            self.repository.add_store(store)
        else:
            store.slug = normalized
            store.version += 1
        translation = self._upsert_translation(
            store,
            locale_code.strip(),
            cleaned_name,
            description,
            seo_title,
            seo_description,
        )
        return _store_view(store, country, translation)

    def save_payout(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_code: str,
        account_type: AccountType,
        currency_code: str,
        account_holder_name: str,
        account_number: str,
        bank_name: str | None,
        is_default: bool,
    ) -> PayoutView:
        """Add a payout destination and keep only the masked number visible."""
        country_id = _country_id(self.countries, country_code)
        self._require(actor, "vendor.store.write", country_id, vendor_id)
        country = self._approved_country(vendor_id, country_id)
        currency = _currency(self.countries, country_id, currency_code)
        holder = _required_text(account_holder_name, "Account holder name is empty.")
        masked = mask_account_number(account_number)
        encrypted = self.cipher.encrypt(account_number.strip())
        if is_default:
            self.repository.clear_default_payouts(country.vendor_country_id, currency)
        account = VendorPayoutAccount(
            vendor_payout_account_id=uuid4(),
            vendor_country_id=country.vendor_country_id,
            account_type=account_type,
            currency_code=currency,
            account_holder_name=holder,
            bank_name=_optional_text(bank_name),
            account_number_masked=masked,
            account_number_encrypted=encrypted,
            is_default=is_default,
            verification_status=VerificationStatus.PENDING,
        )
        self.repository.add_payout(account)
        self._stage(
            actor,
            "vendor.payout.saved",
            "vendor_payout_account",
            account.vendor_payout_account_id,
            country_id,
            vendor_id,
            {"verification_status": VerificationStatus.PENDING.value, "is_default": is_default},
        )
        return _payout_view(account)

    def add_warehouse(
        self,
        actor: AuthContext,
        *,
        vendor_id: UUID,
        country_code: str,
        code: str,
        address_line_1: str,
        city_text: str,
        postal_code: str | None,
        contact_name: str,
        contact_phone: str,
        fulfillment_enabled: bool,
        pickup_enabled: bool,
        status: StorefrontStatus,
    ) -> WarehouseView:
        """Add a warehouse in the owner's country."""
        country_id = _country_id(self.countries, country_code)
        self._require(actor, "vendor.warehouse.write", country_id, vendor_id)
        self._approved_country(vendor_id, country_id)
        normalized_code = _required_text(code, "Warehouse code is empty.").upper()
        if self.repository.find_warehouse_by_code(vendor_id, normalized_code) is not None:
            raise VendorServiceError(409, "Warehouse code is already used.", code="WAREHOUSE_CODE_TAKEN")
        warehouse = Warehouse(
            warehouse_id=uuid4(),
            vendor_id=vendor_id,
            country_id=country_id,
            code=normalized_code,
            address_line_1=_required_text(address_line_1, "Warehouse address is empty."),
            city_text=_required_text(city_text, "Warehouse city is empty."),
            postal_code=_optional_text(postal_code),
            contact_name=_required_text(contact_name, "Warehouse contact name is empty."),
            contact_phone=_required_text(contact_phone, "Warehouse contact phone is empty."),
            fulfillment_enabled=fulfillment_enabled,
            pickup_enabled=pickup_enabled,
            status=status,
        )
        self.repository.add_warehouse(warehouse)
        return _warehouse_view(warehouse)

    def activate(self, actor: AuthContext, *, vendor_id: UUID, country_code: str) -> StoreView:
        """Enable selling only after payout, warehouse, and translation checks pass."""
        country_id = _country_id(self.countries, country_code)
        self._require(actor, "vendor.store.write", country_id, vendor_id)
        country = self._locked_country(vendor_id, country_id)
        store = self.repository.get_store_for_vendor_country(vendor_id, country_id, for_update=True)
        if store is None:
            raise VendorServiceError(404, "Store was not found.")
        currency = _default_currency(self.countries, country_id)
        locale = _default_locale(self.countries, country_id)
        payout = self.repository.get_default_payout(country.vendor_country_id, currency, for_update=True)
        warehouses = self.repository.list_warehouses(vendor_id, country_id, for_update=True)
        translation = self.repository.get_translation(store.store_id, locale)
        conflict = self.repository.find_store_by_country_slug(country_id, store.slug)
        failure = _activation_failure(country, payout, warehouses, translation, conflict, store.store_id)
        if failure is not None:
            raise VendorServiceError(409, "Store cannot go live.", code=failure)
        store.status = StorefrontStatus.ACTIVE
        store.version += 1
        country.payout_enabled = True
        country.selling_enabled = True
        country.version += 1
        self._stage(
            actor,
            "vendor.store.activated",
            "store",
            store.store_id,
            country_id,
            vendor_id,
            {"status": StorefrontStatus.ACTIVE.value, "selling_enabled": True},
        )
        return _store_view(store, country, translation)

    def verify_payout(self, actor: AuthContext, payout_account_id: UUID) -> PayoutView:
        """Verify a payout account when the reviewer is not its owner."""
        account = self.repository.get_payout(payout_account_id, for_update=True)
        if account is None:
            raise VendorServiceError(404, "Payout account was not found.")
        country = self.repository.get_country(account.vendor_country_id, for_update=True)
        if country is None:
            raise VendorServiceError(404, "Seller country application was not found.")
        vendor = self.repository.get_vendor(country.vendor_id, for_update=True)
        if vendor is None:
            raise VendorServiceError(404, "Seller organization was not found.")
        self._require(actor, "vendor.payout.verify", country.country_id, None)
        if actor.user_id == vendor.created_by_user_id:
            raise VendorServiceError(403, "The owner cannot verify this payout account.")
        if account.verification_status is VerificationStatus.PENDING:
            account.verification_status = VerificationStatus.VERIFIED
            account.verified_by = actor.user_id
            account.verified_at = self.clock()
            self._stage(
                actor,
                "vendor.payout.verified",
                "vendor_payout_account",
                account.vendor_payout_account_id,
                country.country_id,
                country.vendor_id,
                {"verification_status": VerificationStatus.VERIFIED.value},
            )
        return _payout_view(account)

    def _require(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> None:
        """Reject an actor who lacks the permission in this country."""
        if not self.authorizer.allows(actor, permission, country_id, vendor_id):
            raise VendorServiceError(403, "Store action is outside the assigned country.")

    def _approved_country(self, vendor_id: UUID, country_id: UUID) -> VendorCountry:
        """Return the approved country row for one organization."""
        country = self.repository.get_country_for_market(vendor_id, country_id)
        if country is None:
            raise VendorServiceError(404, "Seller country application was not found.")
        if country.onboarding_status is not OnboardingStatus.APPROVED:
            raise VendorServiceError(409, "Seller country is not approved.", code="APPLICATION_NOT_APPROVED")
        return country

    def _locked_country(self, vendor_id: UUID, country_id: UUID) -> VendorCountry:
        """Lock the approved country row."""
        country = self._approved_country(vendor_id, country_id)
        locked = self.repository.get_country(country.vendor_country_id, for_update=True)
        if locked is None:
            raise VendorServiceError(404, "Seller country application was not found.")
        return locked

    def _require_locale(self, country_id: UUID, locale_code: str) -> None:
        """Reject a locale the country has not enabled."""
        if not self.countries.is_locale_enabled(country_id, locale_code.strip()):
            raise VendorServiceError(422, "Locale is not enabled.", code="LOCALE_UNAVAILABLE")

    def _require_available_slug(self, country_id: UUID, slug: str, vendor_id: UUID) -> None:
        """Reject a slug already used by another store in the country."""
        existing = self.repository.find_store_by_country_slug(country_id, slug)
        if existing is not None and existing.vendor_id != vendor_id:
            raise VendorServiceError(409, "Store slug is already used.", code="SLUG_TAKEN")

    def _upsert_translation(
        self,
        store: Store,
        locale_code: str,
        name: str,
        description: str | None,
        seo_title: str | None,
        seo_description: str | None,
    ) -> StoreTranslation:
        """Insert or replace one store translation."""
        current = self.repository.get_translation(store.store_id, locale_code)
        if current is None:
            current = StoreTranslation(
                store_id=store.store_id,
                locale_code=locale_code,
                name=name,
                description=_optional_text(description),
                seo_title=_optional_text(seo_title),
                seo_description=_optional_text(seo_description),
            )
            self.repository.add_translation(current)
            return current
        current.name = name
        current.description = _optional_text(description)
        current.seo_title = _optional_text(seo_title)
        current.seo_description = _optional_text(seo_description)
        return current

    def _stage(
        self,
        actor: AuthContext,
        action: str,
        resource_type: str,
        resource_id: UUID,
        country_id: UUID,
        vendor_id: UUID,
        after_data: dict[str, object],
    ) -> None:
        """Stage an audit event that contains ids and the decision only."""
        self.audit.stage(
            AuditEventInput(
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                correlation_id=actor.correlation_id,
                actor_user_id=actor.user_id,
                country_id=country_id,
                vendor_id=vendor_id,
                after_data=after_data,
            ),
            self.transaction,
        )


def normalize_slug(value: str) -> str:
    """Normalize a store slug to lowercase ASCII words.

    Args:
        value: Caller-supplied slug.

    Returns:
        Hyphenated lowercase slug.

    Raises:
        VendorServiceError: If the slug has no usable characters.
    """
    pieces: list[str] = []
    current: list[str] = []
    for char in value.strip().lower():
        if char.isascii() and char.isalnum():
            current.append(char)
            continue
        if current and char in {" ", "-", "_"}:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    slug = "-".join(pieces)
    if not slug:
        raise VendorServiceError(422, "Store slug is empty.")
    return slug


def mask_account_number(account_number: str) -> str:
    """Return a display mask that keeps the last four characters.

    Args:
        account_number: Raw account number.

    Returns:
        Masked account number.

    Raises:
        VendorServiceError: If the number is too short to mask.
    """
    cleaned = account_number.strip()
    if len(cleaned) < 4 or any(char.isspace() for char in cleaned):
        raise VendorServiceError(422, "Account number is invalid.")
    return f"****{cleaned[-4:]}"


def _country_id(countries: CountryReader, country_code: str) -> UUID:
    """Resolve a country code to its identifier."""
    try:
        return countries.resolve_country(country_code).country_id
    except (KeyError, ValueError, UnicodeError) as exc:
        raise VendorServiceError(422, "Country code is invalid.") from exc


def _currency(countries: CountryReader, country_id: UUID, currency_code: str) -> str:
    """Return an enabled three-letter currency code."""
    currency = currency_code.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise VendorServiceError(422, "Currency code is invalid.")
    if not countries.is_currency_enabled(country_id, currency):
        raise VendorServiceError(422, "Currency is not enabled.", code="CURRENCY_UNAVAILABLE")
    return currency


def _default_currency(countries: CountryReader, country_id: UUID) -> str:
    """Return the country settlement currency."""
    try:
        return countries.default_currency_code(country_id)
    except KeyError as exc:
        raise VendorServiceError(409, "Store cannot go live.", code="PAYOUT_NOT_VERIFIED") from exc


def _default_locale(countries: CountryReader, country_id: UUID) -> str:
    """Return the country default locale."""
    try:
        return countries.default_locale_code(country_id)
    except KeyError as exc:
        raise VendorServiceError(409, "Store cannot go live.", code="TRANSLATION_MISSING") from exc


def _required_text(value: str, message: str) -> str:
    """Return stripped text or reject a blank value."""
    cleaned = value.strip()
    if not cleaned:
        raise VendorServiceError(422, message)
    return cleaned


def _optional_text(value: str | None) -> str | None:
    """Return stripped text, or none when the value is blank."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _activation_failure(
    country: VendorCountry,
    payout: VendorPayoutAccount | None,
    warehouses: list[Warehouse],
    translation: StoreTranslation | None,
    conflict: Store | None,
    store_id: UUID,
) -> str | None:
    """Return the first failed go-live check, if any."""
    if country.onboarding_status is not OnboardingStatus.APPROVED:
        return "ONBOARDING_NOT_APPROVED"
    if payout is None or payout.verification_status is not VerificationStatus.VERIFIED:
        return "PAYOUT_NOT_VERIFIED"
    ready = any(
        row.status is StorefrontStatus.ACTIVE and row.fulfillment_enabled for row in warehouses
    )
    if not ready:
        return "WAREHOUSE_NOT_READY"
    if translation is None or not translation.name.strip():
        return "TRANSLATION_MISSING"
    if conflict is not None and conflict.store_id != store_id:
        return "SLUG_TAKEN"
    return None


def _store_view(store: Store, country: VendorCountry, translation: StoreTranslation) -> StoreView:
    """Build a store response from the saved rows."""
    return StoreView(
        store.store_id,
        store.vendor_id,
        store.country_id,
        store.slug,
        store.status,
        translation.locale_code,
        translation.name,
        country.selling_enabled,
        country.payout_enabled,
    )


def _payout_view(account: VendorPayoutAccount) -> PayoutView:
    """Build a payout response that omits the ciphertext."""
    return PayoutView(
        account.vendor_payout_account_id,
        account.currency_code,
        account.account_number_masked,
        account.is_default,
        account.verification_status,
    )


def _warehouse_view(warehouse: Warehouse) -> WarehouseView:
    """Build a warehouse response."""
    return WarehouseView(
        warehouse.warehouse_id,
        warehouse.vendor_id,
        warehouse.country_id,
        warehouse.code,
        warehouse.status,
        warehouse.fulfillment_enabled,
    )
