"""Publish one seller offer for one shared SKU."""

import base64
import binascii
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.auth_context import AuthContext
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.models import (
    MediaAsset,
    OfferCountry,
    CatalogProduct,
    ProductAttributeValue,
    ProductMedia,
    ProductTranslation,
    SellerOffer,
    Sku,
    SkuAttributeValue,
)
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.service import SellerApprovalReader, StepUpConsumer, TaxonomyAuthorizer
from app.modules.catalog.types import (
    ModerationStatus,
    OfferStatus,
    ProductStatus,
    PublicationStatus,
    RecordStatus,
    ReviewDecision,
)
from app.ports.country import CountryReader
from app.ports.document_storage import DocumentStorage
from app.ports.vendor_scope import VendorScopeReader

PRODUCT_WRITE = "catalog.product.write"
PRODUCT_MODERATE = "catalog.product.moderate"
OFFER_PUBLISH = "catalog.offer.publish"


@dataclass(frozen=True, slots=True)
class AttributeInput:
    """Describe one product or SKU attribute value."""

    attribute_id: UUID
    option_id: UUID | None = None
    value_text: str | None = None
    value_number: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ProductView:
    """Expose one shared product."""

    product_id: UUID
    status: ProductStatus


@dataclass(frozen=True, slots=True)
class SkuView:
    """Expose one shared SKU."""

    sku_id: UUID
    product_id: UUID
    sku_code: str


@dataclass(frozen=True, slots=True)
class MediaView:
    """Expose media metadata to the uploading vendor."""

    media_id: UUID
    storage_key: str
    media_type: str
    sort_order: int
    is_primary: bool


@dataclass(frozen=True, slots=True)
class OfferView:
    """Expose one seller offer and its country price."""

    offer_id: UUID
    vendor_id: UUID
    store_id: UUID
    sku_id: UUID
    status: OfferStatus
    publication_status: PublicationStatus
    currency_code: str
    list_price: Decimal


@dataclass(frozen=True, slots=True)
class PublicOfferView:
    """Expose a published offer without media keys or stock."""

    offer_id: UUID
    store_id: UUID
    sku_id: UUID
    product_id: UUID
    currency_code: str
    list_price: Decimal
    name: str


class CatalogOfferService:
    """Create shared catalog rows and publish one offer per seller."""

    def __init__(
        self,
        repository: CatalogRepository,
        countries: CountryReader,
        authorizer: TaxonomyAuthorizer,
        sellers: SellerApprovalReader,
        scope: VendorScopeReader,
        storage: DocumentStorage,
        consume_step_up: StepUpConsumer,
    ) -> None:
        """Initialize offer dependencies."""
        self.repository = repository
        self.countries = countries
        self.authorizer = authorizer
        self.sellers = sellers
        self.scope = scope
        self.storage = storage
        self.consume_step_up = consume_step_up

    def create_product(
        self,
        actor: AuthContext,
        vendor_id: UUID,
        country_code: str,
        category_id: UUID,
        brand_id: UUID,
        locale_code: str,
        name: str,
        description: str | None,
        attributes: list[AttributeInput],
    ) -> ProductView:
        """Create a draft shared product in an enabled category and brand."""
        country_id = _country_id(self.countries, country_code)
        self._require_writer(actor, vendor_id, country_id)
        self._require_taxonomy(category_id, brand_id, country_id)
        _require_locale(self.countries, country_id, locale_code)
        product = CatalogProduct(
            product_id=uuid4(),
            brand_id=brand_id,
            category_id=category_id,
            status=ProductStatus.DRAFT,
            created_by=actor.user_id,
        )
        self.repository.add_product(product)
        self.repository.add_product_translation(
            ProductTranslation(
                product_id=product.product_id,
                locale_code=locale_code,
                name=name.strip(),
                description=description,
            )
        )
        _store_attributes(self.repository, product.product_id, attributes)
        return ProductView(product_id=product.product_id, status=product.status)

    def add_sku(
        self,
        actor: AuthContext,
        product_id: UUID,
        vendor_id: UUID,
        country_code: str,
        sku_code: str,
        attributes: list[AttributeInput],
    ) -> SkuView:
        """Add one variant to a shared product."""
        country_id = _country_id(self.countries, country_code)
        self._require_writer(actor, vendor_id, country_id)
        _require_product(self.repository, product_id)
        sku = Sku(sku_id=uuid4(), product_id=product_id, sku_code=sku_code.strip())
        self.repository.add_sku(sku)
        for value in attributes:
            _one_value(value)
            self.repository.add_sku_attribute(
                SkuAttributeValue(
                    sku_id=sku.sku_id,
                    attribute_id=value.attribute_id,
                    option_id=value.option_id,
                    value_text=value.value_text,
                    value_number=value.value_number,
                )
            )
        return SkuView(sku_id=sku.sku_id, product_id=product_id, sku_code=sku.sku_code)

    def attach_media(
        self,
        actor: AuthContext,
        product_id: UUID,
        vendor_id: UUID,
        country_code: str,
        content_base64: str,
        media_type: str,
        sort_order: int,
        is_primary: bool,
    ) -> MediaView:
        """Store private media and attach it to a product."""
        country_id = _country_id(self.countries, country_code)
        self._require_writer(actor, vendor_id, country_id)
        _require_product(self.repository, product_id)
        content = _decode_content(content_base64)
        storage_key = self.storage.put_private(content, media_type)
        if is_primary:
            self.repository.clear_primary_media(product_id)
        media = MediaAsset(
            media_id=uuid4(),
            storage_key=storage_key,
            media_type=media_type,
            vendor_id=vendor_id,
            created_by=actor.user_id,
        )
        self.repository.add_media(media)
        self.repository.add_product_media(
            ProductMedia(
                product_id=product_id,
                media_id=media.media_id,
                sort_order=sort_order,
                is_primary=is_primary,
            )
        )
        return MediaView(
            media_id=media.media_id,
            storage_key=storage_key,
            media_type=media_type,
            sort_order=sort_order,
            is_primary=is_primary,
        )

    def submit_product(
        self,
        actor: AuthContext,
        product_id: UUID,
        vendor_id: UUID,
        country_codes: list[str],
    ) -> ProductView:
        """Mark each target country pending after taxonomy and attribute checks."""
        product = _require_product(self.repository, product_id)
        country_ids = [self._checked_country(actor, vendor_id, product, code) for code in country_codes]
        _require_attributes(self.repository, product)
        for country_id in country_ids:
            self.repository.set_product_moderation(
                product.product_id,
                country_id,
                ModerationStatus.PENDING,
            )
        return ProductView(product_id=product.product_id, status=product.status)

    def moderate_product(
        self,
        actor: AuthContext,
        product_id: UUID,
        country_code: str,
        decision: ReviewDecision,
        step_up_token: str,
    ) -> ProductView:
        """Approve or reject one country without changing the others."""
        country_id = _country_id(self.countries, country_code)
        if not self.authorizer.allows(actor, PRODUCT_MODERATE, country_id, None):
            raise CatalogServiceError(403, "Product moderation is not allowed.")
        product = _require_product(self.repository, product_id)
        self.consume_step_up(step_up_token, actor, PRODUCT_MODERATE)
        locked = self.repository.get_product(product.product_id, for_update=True)
        if locked is None:
            raise CatalogServiceError(404, "Product was not found.")
        status = (
            ModerationStatus.APPROVED
            if decision is ReviewDecision.APPROVED
            else ModerationStatus.REJECTED
        )
        self.repository.set_product_moderation(locked.product_id, country_id, status)
        if decision is ReviewDecision.APPROVED:
            locked.status = ProductStatus.ACTIVE
        return ProductView(product_id=locked.product_id, status=locked.status)

    def create_offer(
        self,
        actor: AuthContext,
        vendor_id: UUID,
        store_id: UUID,
        sku_id: UUID,
        country_code: str,
        seller_sku: str,
        list_price: Decimal,
        currency_code: str,
    ) -> OfferView:
        """Create a draft offer for one approved SKU in one selling country."""
        country_id = _country_id(self.countries, country_code)
        self._require_publisher(actor, vendor_id, country_id)
        self._require_selling(vendor_id, country_id)
        if not self.scope.owns_store(vendor_id, store_id, country_id):
            raise CatalogServiceError(422, "Store does not belong to this vendor.", "STORE_NOT_OWNED")
        sku = self._approved_sku(sku_id, country_id)
        price = _price(list_price)
        currency = _currency(self.countries, country_id, currency_code)
        offer = SellerOffer(
            offer_id=uuid4(),
            vendor_id=vendor_id,
            store_id=store_id,
            sku_id=sku.sku_id,
            seller_sku=seller_sku.strip(),
            status=OfferStatus.DRAFT,
            version=1,
        )
        self.repository.add_offer(offer)
        self.repository.add_offer_country(
            OfferCountry(
                offer_id=offer.offer_id,
                country_id=country_id,
                publication_status=PublicationStatus.DRAFT,
                currency_code=currency,
                list_price=price,
            )
        )
        return _offer_view(offer, PublicationStatus.DRAFT, currency, price)

    def publish_offer(
        self,
        actor: AuthContext,
        offer_id: UUID,
        country_code: str,
        step_up_token: str,
    ) -> OfferView:
        """Publish one offer country when selling and moderation still allow it."""
        offer = self.repository.get_offer(offer_id)
        if offer is None or not self.scope.is_active_member(actor.user_id, offer.vendor_id):
            raise CatalogServiceError(404, "Offer was not found.")
        country_id = _country_id(self.countries, country_code)
        if not self.authorizer.allows(actor, OFFER_PUBLISH, country_id, offer.vendor_id):
            raise CatalogServiceError(403, "Offer publication is not allowed.")
        current = self.repository.get_offer_country(offer_id, country_id)
        if current is None:
            raise CatalogServiceError(404, "Offer was not found.")
        self.consume_step_up(step_up_token, actor, OFFER_PUBLISH)
        locked = self.repository.get_offer(offer_id, for_update=True)
        row = self.repository.get_offer_country(offer_id, country_id, for_update=True)
        if locked is None or row is None:
            raise CatalogServiceError(404, "Offer was not found.")
        self._sellable_sku(locked.sku_id, locked.vendor_id, country_id)
        row.publication_status = PublicationStatus.PUBLISHED
        locked.status = OfferStatus.ACTIVE
        return _offer_view(locked, row.publication_status, row.currency_code, row.list_price)

    def list_offers(self, country_code: str, locale_code: str) -> list[PublicOfferView]:
        """Return published offers for one country and locale."""
        country_id = _country_id(self.countries, country_code)
        _require_locale(self.countries, country_id, locale_code)
        views: list[PublicOfferView] = []
        for offer, row, sku, product, moderation in self.repository.list_published_offers(country_id):
            view = _public_view(self.repository, offer, row, sku, product, moderation, locale_code)
            if view is not None:
                views.append(view)
        return views

    def _require_writer(self, actor: AuthContext, vendor_id: UUID, country_id: UUID) -> None:
        """Require an approved vendor writer."""
        if not self.sellers.is_approved(vendor_id, country_id):
            raise CatalogServiceError(403, "Vendor is not approved in this country.")
        if not self.authorizer.allows(actor, PRODUCT_WRITE, country_id, vendor_id):
            raise CatalogServiceError(403, "Product changes are not allowed.")

    def _require_publisher(self, actor: AuthContext, vendor_id: UUID, country_id: UUID) -> None:
        """Require permission to publish an offer for this vendor."""
        if not self.authorizer.allows(actor, OFFER_PUBLISH, country_id, vendor_id):
            raise CatalogServiceError(403, "Offer publication is not allowed.")

    def _require_selling(self, vendor_id: UUID, country_id: UUID) -> None:
        """Require go-live selling for one vendor country."""
        if not self.scope.is_selling_enabled(vendor_id, country_id):
            raise CatalogServiceError(409, "Selling is not enabled.", "SELLING_DISABLED")

    def _require_taxonomy(self, category_id: UUID, brand_id: UUID, country_id: UUID) -> None:
        """Require the category and brand to be enabled in the country."""
        if not _category_enabled(self.repository, category_id, country_id):
            raise CatalogServiceError(409, "Category is not enabled.", "CATEGORY_DISABLED")
        if not self.repository.is_brand_enabled(brand_id, country_id):
            raise CatalogServiceError(409, "Brand is not enabled.", "BRAND_DISABLED")

    def _checked_country(
        self,
        actor: AuthContext,
        vendor_id: UUID,
        product: CatalogProduct,
        country_code: str,
    ) -> UUID:
        """Authorize one submit target and confirm its taxonomy."""
        country_id = _country_id(self.countries, country_code)
        self._require_writer(actor, vendor_id, country_id)
        self._require_taxonomy(product.category_id, product.brand_id, country_id)
        return country_id

    def _approved_sku(self, sku_id: UUID, country_id: UUID) -> Sku:
        """Return a SKU whose product is approved in the country."""
        sku = self.repository.get_sku(sku_id)
        if sku is None:
            raise CatalogServiceError(404, "SKU was not found.")
        moderation = self.repository.get_product_country(sku.product_id, country_id)
        if moderation is None or moderation.moderation_status is not ModerationStatus.APPROVED:
            raise CatalogServiceError(409, "Product is not approved.", "PRODUCT_NOT_APPROVED")
        return sku

    def _sellable_sku(self, sku_id: UUID, vendor_id: UUID, country_id: UUID) -> Sku:
        """Re-check publication rules after the offer lock."""
        self._require_selling(vendor_id, country_id)
        sku = self._approved_sku(sku_id, country_id)
        product = _require_product(self.repository, sku.product_id)
        if product.status is ProductStatus.RETIRED:
            raise CatalogServiceError(409, "Product is not approved.", "PRODUCT_NOT_APPROVED")
        if not _category_enabled(self.repository, product.category_id, country_id):
            raise CatalogServiceError(409, "Category is not enabled.", "CATEGORY_DISABLED")
        return sku


def _store_attributes(
    repository: CatalogRepository,
    product_id: UUID,
    attributes: list[AttributeInput],
) -> None:
    """Store product attribute values that each use one representation."""
    for value in attributes:
        _one_value(value)
        repository.add_product_attribute(
            ProductAttributeValue(
                product_id=product_id,
                attribute_id=value.attribute_id,
                option_id=value.option_id,
                value_text=value.value_text,
                value_number=value.value_number,
            )
        )


def _require_attributes(repository: CatalogRepository, product: CatalogProduct) -> None:
    """Reject a submit that omits a required category attribute."""
    required = set(repository.list_required_attribute_ids(product.category_id))
    present = set(repository.list_product_attribute_ids(product.product_id))
    if not required.issubset(present):
        raise CatalogServiceError(422, "Required attributes are missing.", "ATTRIBUTES_MISSING")


def _one_value(value: AttributeInput) -> None:
    """Require exactly one of option, text, or number."""
    filled = [
        value.option_id is not None,
        value.value_text is not None,
        value.value_number is not None,
    ]
    if sum(filled) != 1:
        raise CatalogServiceError(422, "Each attribute needs exactly one value.")


def _require_product(repository: CatalogRepository, product_id: UUID) -> CatalogProduct:
    """Return a product or reject a missing id."""
    product = repository.get_product(product_id)
    if product is None:
        raise CatalogServiceError(404, "Product was not found.")
    return product


def _category_enabled(repository: CatalogRepository, category_id: UUID, country_id: UUID) -> bool:
    """Return whether the category and its parents can be sold."""
    if not repository.is_category_enabled(category_id, country_id):
        return False
    category = repository.get_category(category_id)
    seen: set[UUID] = set()
    parent_id = None if category is None else category.parent_id
    while parent_id is not None:
        if parent_id in seen:
            return False
        seen.add(parent_id)
        parent = repository.get_category(parent_id)
        if parent is None or parent.status is not RecordStatus.ACTIVE:
            return False
        parent_id = parent.parent_id
    return category is not None


def _public_view(repository, offer, row, sku, product, moderation, locale_code: str):
    """Hide drafts, retired products, and rows without a locale name."""
    if offer.status is not OfferStatus.ACTIVE:
        return None
    if row.publication_status is not PublicationStatus.PUBLISHED:
        return None
    if moderation.moderation_status is not ModerationStatus.APPROVED:
        return None
    if product.status is ProductStatus.RETIRED:
        return None
    name = repository.get_product_name(sku.product_id, locale_code)
    if name is None:
        return None
    return PublicOfferView(
        offer_id=offer.offer_id,
        store_id=offer.store_id,
        sku_id=sku.sku_id,
        product_id=sku.product_id,
        currency_code=row.currency_code,
        list_price=row.list_price,
        name=name,
    )


def _offer_view(offer: SellerOffer, publication: PublicationStatus, currency: str, price: Decimal) -> OfferView:
    """Build an owner offer view."""
    return OfferView(
        offer_id=offer.offer_id,
        vendor_id=offer.vendor_id,
        store_id=offer.store_id,
        sku_id=offer.sku_id,
        status=offer.status,
        publication_status=publication,
        currency_code=currency,
        list_price=price,
    )


def _price(list_price: Decimal) -> Decimal:
    """Reject a zero or negative price."""
    if list_price <= 0:
        raise CatalogServiceError(422, "List price must be greater than zero.")
    return list_price


def _currency(countries: CountryReader, country_id: UUID, currency_code: str) -> str:
    """Require the country's settlement currency."""
    try:
        expected = countries.default_currency_code(country_id)
    except KeyError as exc:
        raise CatalogServiceError(422, "Country currency is unavailable.") from exc
    if currency_code.upper() != expected:
        raise CatalogServiceError(422, "Currency does not match the country.")
    return expected


def _require_locale(countries: CountryReader, country_id: UUID, locale_code: str) -> None:
    """Reject a locale the country does not enable."""
    if not countries.is_locale_enabled(country_id, locale_code):
        raise CatalogServiceError(422, "Locale is not enabled.")


def _country_id(countries: CountryReader, country_code: str) -> UUID:
    """Resolve a country code to its identifier."""
    try:
        return countries.resolve_country(country_code).country_id
    except (KeyError, ValueError, UnicodeError) as exc:
        raise CatalogServiceError(422, "Country code is invalid.") from exc


def _decode_content(content_base64: str) -> bytes:
    """Decode media bytes without accepting a client storage key."""
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CatalogServiceError(422, "Media content is invalid.") from exc
    if not content:
        raise CatalogServiceError(422, "Media content is empty.")
    return content
