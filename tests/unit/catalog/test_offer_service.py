"""Test shared products and per-seller offers."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.auth_context import AuthContext
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.models import OfferCountry, CatalogProduct, ProductCountry, SellerOffer, Sku
from app.modules.catalog.offers import CatalogOfferService
from app.modules.catalog.schemas import OfferCreateRequest, PublicOfferResponse
from app.modules.catalog.types import (
    ModerationStatus,
    OfferStatus,
    ProductStatus,
    PublicationStatus,
    RecordStatus,
    ReviewDecision,
)
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.grants import AuthGrantError
from app.ports.document_storage import MemoryDocumentStorage

NOW = datetime(2026, 10, 28, tzinfo=UTC)
OWNER = UUID(int=1)
OTHER = UUID(int=2)
STAFF = UUID(int=3)
REVIEWER = UUID(int=4)
VENDOR = UUID(int=5)
OTHER_VENDOR = UUID(int=6)


@dataclass
class OfferRepo:
    """Store catalog rows in memory."""

    products: list[CatalogProduct] = field(default_factory=list)
    countries: list[ProductCountry] = field(default_factory=list)
    translations: list = field(default_factory=list)
    attributes: list = field(default_factory=list)
    skus: list[Sku] = field(default_factory=list)
    sku_attributes: list = field(default_factory=list)
    offers: list[SellerOffer] = field(default_factory=list)
    offer_countries: list[OfferCountry] = field(default_factory=list)
    enabled_categories: set[UUID] = field(default_factory=set)
    enabled_brands: set[UUID] = field(default_factory=set)
    required: set[UUID] = field(default_factory=set)

    def is_category_enabled(self, category_id: UUID, country_id: UUID) -> bool:
        """Return the test category flag."""
        del country_id
        return category_id in self.enabled_categories

    def is_brand_enabled(self, brand_id: UUID, country_id: UUID) -> bool:
        """Return the test brand flag."""
        del country_id
        return brand_id in self.enabled_brands

    def get_category(self, category_id: UUID):
        """Return an active category with no parent."""
        return SimpleNamespace(
            category_id=category_id,
            parent_id=None,
            status=RecordStatus.ACTIVE,
        )

    def list_required_attribute_ids(self, category_id: UUID) -> list[UUID]:
        """Return configured required attributes."""
        del category_id
        return list(self.required)

    def list_product_attribute_ids(self, product_id: UUID) -> list[UUID]:
        """Return stored product attribute ids."""
        return [row.attribute_id for row in self.attributes if row.product_id == product_id]

    def add_product(self, product: CatalogProduct) -> None:
        """Remember one product."""
        self.products.append(product)

    def add_product_translation(self, translation) -> None:
        """Remember one translation."""
        self.translations.append(translation)

    def add_product_attribute(self, value) -> None:
        """Remember one product attribute."""
        self.attributes.append(value)

    def get_product(self, product_id: UUID, *, for_update: bool = False) -> CatalogProduct | None:
        """Return one product."""
        del for_update
        return next((row for row in self.products if row.product_id == product_id), None)

    def get_product_country(self, product_id: UUID, country_id: UUID, *, for_update: bool = False):
        """Return one moderation row."""
        del for_update
        return next(
            (
                row
                for row in self.countries
                if row.product_id == product_id and row.country_id == country_id
            ),
            None,
        )

    def set_product_moderation(self, product_id: UUID, country_id: UUID, status: ModerationStatus) -> None:
        """Insert or update one country."""
        row = self.get_product_country(product_id, country_id)
        if row is None:
            self.countries.append(
                ProductCountry(
                    product_id=product_id,
                    country_id=country_id,
                    moderation_status=status,
                )
            )
            return
        row.moderation_status = status

    def add_sku(self, sku: Sku) -> None:
        """Remember one SKU."""
        self.skus.append(sku)

    def add_sku_attribute(self, value) -> None:
        """Remember one SKU attribute."""
        self.sku_attributes.append(value)

    def get_sku(self, sku_id: UUID) -> Sku | None:
        """Return one SKU."""
        return next((row for row in self.skus if row.sku_id == sku_id), None)

    def clear_primary_media(self, product_id: UUID) -> None:
        """Ignore media order in the unit fake."""
        del product_id

    def add_media(self, media) -> None:
        """Ignore media bytes in the unit fake."""
        del media

    def add_product_media(self, link) -> None:
        """Ignore media links in the unit fake."""
        del link

    def add_offer(self, offer: SellerOffer) -> None:
        """Remember one offer."""
        self.offers.append(offer)

    def add_offer_country(self, row: OfferCountry) -> None:
        """Remember one offer country."""
        self.offer_countries.append(row)

    def get_offer(self, offer_id: UUID, *, for_update: bool = False) -> SellerOffer | None:
        """Return one offer."""
        del for_update
        return next((row for row in self.offers if row.offer_id == offer_id), None)

    def get_offer_country(self, offer_id: UUID, country_id: UUID, *, for_update: bool = False):
        """Return one offer country."""
        del for_update
        return next(
            (
                row
                for row in self.offer_countries
                if row.offer_id == offer_id and row.country_id == country_id
            ),
            None,
        )

    def get_product_name(self, product_id: UUID, locale_code: str) -> str | None:
        """Return one translation name."""
        row = next(
            (
                item
                for item in self.translations
                if item.product_id == product_id and item.locale_code == locale_code
            ),
            None,
        )
        if row is None:
            return None
        return row.name

    def list_published_offers(self, country_id: UUID):
        """Join stored rows for one country."""
        found = []
        for offer in self.offers:
            row = self.get_offer_country(offer.offer_id, country_id)
            sku = self.get_sku(offer.sku_id)
            if row is None or sku is None:
                continue
            product = self.get_product(sku.product_id)
            moderation = self.get_product_country(sku.product_id, country_id)
            if product is None or moderation is None:
                continue
            found.append((offer, row, sku, product, moderation))
        return found


class Authorizer:
    """Allow owners to write and reviewers to moderate."""

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Apply the test permission map."""
        del country_id
        if actor.user_id == REVIEWER:
            return vendor_id is None and permission == "catalog.product.moderate"
        if actor.user_id == STAFF:
            return False
        if actor.user_id in {OWNER, OTHER} and vendor_id is not None:
            return permission in {"catalog.product.write", "catalog.offer.publish"}
        return False

    def allows_global(self, actor: AuthContext, permission: str) -> bool:
        """Deny global catalog writes in these tests."""
        del actor, permission
        return False


class Sellers:
    """Treat every vendor country as approved."""

    def is_approved(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return approval."""
        del vendor_id, country_id
        return True


class Scope:
    """Control selling, store ownership, and membership."""

    def __init__(self) -> None:
        """Start with selling enabled."""
        self.selling = True
        self.members: set[tuple[UUID, UUID]] = set()
        self.stores: dict[UUID, tuple[UUID, UUID]] = {}

    def is_selling_enabled(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return the test selling flag."""
        del vendor_id, country_id
        return self.selling

    def owns_store(self, vendor_id: UUID, store_id: UUID, country_id: UUID) -> bool:
        """Return whether the store mapping matches."""
        return self.stores.get(store_id) == (vendor_id, country_id)

    def is_active_member(self, user_id: UUID, vendor_id: UUID) -> bool:
        """Return whether the pair was registered."""
        return (user_id, vendor_id) in self.members


class Consume:
    """Reject every token except the test grant."""

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Raise before a mutation when the token is wrong."""
        del actor, action_class
        if token != "ok":
            raise AuthGrantError("bad")


def _service(repo: OfferRepo, scope: Scope) -> CatalogOfferService:
    """Build an offer service over the memory repository."""
    return CatalogOfferService(
        repo,
        DevelopmentCountryReader(),
        Authorizer(),
        Sellers(),
        scope,
        MemoryDocumentStorage(),
        Consume(),
    )


def _actor(user_id: UUID) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())


def _catalog() -> tuple[OfferRepo, Scope, CatalogOfferService, UUID, UUID]:
    """Return a service with one enabled category and brand."""
    repo = OfferRepo()
    scope = Scope()
    category_id = uuid4()
    brand_id = uuid4()
    repo.enabled_categories.add(category_id)
    repo.enabled_brands.add(brand_id)
    scope.members.add((OWNER, VENDOR))
    scope.members.add((OTHER, OTHER_VENDOR))
    scope.members.add((STAFF, VENDOR))
    return repo, scope, _service(repo, scope), category_id, brand_id


def _product(service: CatalogOfferService, category_id: UUID, brand_id: UUID):
    """Create one draft product."""
    return service.create_product(
        _actor(OWNER),
        VENDOR,
        "NP",
        category_id,
        brand_id,
        "ne",
        "Runner",
        None,
        [],
    )


def test_two_sellers_share_one_product() -> None:
    """Keep one product when two vendors offer the same SKU."""
    repo, scope, service, category_id, brand_id = _catalog()
    product = _product(service, category_id, brand_id)
    sku = service.add_sku(_actor(OWNER), product.product_id, VENDOR, "NP", "RED-1", [])
    service.submit_product(_actor(OWNER), product.product_id, VENDOR, ["NP"])
    service.moderate_product(_actor(REVIEWER), product.product_id, "NP", ReviewDecision.APPROVED, "ok")
    nepal = DevelopmentCountryReader().resolve_country("NP").country_id
    for vendor_id, store_label in ((VENDOR, "a"), (OTHER_VENDOR, "b")):
        store_id = uuid4()
        scope.stores[store_id] = (vendor_id, nepal)
        actor = OWNER if vendor_id == VENDOR else OTHER
        service.create_offer(
            _actor(actor),
            vendor_id,
            store_id,
            sku.sku_id,
            "NP",
            f"seller-{store_label}",
            Decimal("10.00"),
            "NPR",
        )
    assert len(repo.products) == 1
    assert len(repo.offers) == 2


def test_publish_rejects_a_disabled_category_and_disabled_selling() -> None:
    """Keep an offer draft when the country cannot sell it."""
    repo, scope, service, category_id, brand_id = _catalog()
    offer = _approved_offer(repo, scope, service, category_id, brand_id)
    repo.enabled_categories.clear()
    with pytest.raises(CatalogServiceError) as disabled:
        service.publish_offer(_actor(OWNER), offer.offer_id, "NP", "ok")
    assert disabled.value.code == "CATEGORY_DISABLED"
    assert offer.status is OfferStatus.DRAFT
    repo.enabled_categories.add(category_id)
    scope.selling = False
    with pytest.raises(CatalogServiceError) as selling:
        service.publish_offer(_actor(OWNER), offer.offer_id, "NP", "ok")
    assert selling.value.code == "SELLING_DISABLED"


def test_nepal_approval_does_not_publish_the_uae() -> None:
    """Leave the UAE unpublished when only Nepal is approved."""
    repo, scope, service, category_id, brand_id = _catalog()
    offer = _approved_offer(repo, scope, service, category_id, brand_id)
    service.publish_offer(_actor(OWNER), offer.offer_id, "NP", "ok")
    uae = DevelopmentCountryReader().resolve_country("AE").country_id
    assert service.list_offers("AE", "en") == []
    assert repo.get_product_country(repo.products[0].product_id, uae) is None
    assert len(service.list_offers("NP", "ne")) == 1


def test_pending_product_and_zero_price_are_rejected() -> None:
    """Refuse publication and a non-positive price."""
    repo, scope, service, category_id, brand_id = _catalog()
    product = _product(service, category_id, brand_id)
    sku = service.add_sku(_actor(OWNER), product.product_id, VENDOR, "NP", "RED-1", [])
    service.submit_product(_actor(OWNER), product.product_id, VENDOR, ["NP"])
    nepal = DevelopmentCountryReader().resolve_country("NP").country_id
    store_id = uuid4()
    scope.stores[store_id] = (VENDOR, nepal)
    with pytest.raises(CatalogServiceError) as pending:
        service.create_offer(
            _actor(OWNER),
            VENDOR,
            store_id,
            sku.sku_id,
            "NP",
            "seller-a",
            Decimal("10.00"),
            "NPR",
        )
    assert pending.value.code == "PRODUCT_NOT_APPROVED"
    service.moderate_product(_actor(REVIEWER), product.product_id, "NP", ReviewDecision.APPROVED, "ok")
    with pytest.raises(CatalogServiceError) as price:
        service.create_offer(
            _actor(OWNER),
            VENDOR,
            store_id,
            sku.sku_id,
            "NP",
            "seller-a",
            Decimal("0"),
            "NPR",
        )
    assert price.value.status_code == 422
    assert repo.offers == []


def test_offer_payloads_have_no_quantity() -> None:
    """Reject a stock count on the offer request and public response."""
    assert "quantity" not in OfferCreateRequest.model_fields
    assert "quantity" not in PublicOfferResponse.model_fields
    assert "storage_key" not in PublicOfferResponse.model_fields
    with pytest.raises(ValidationError):
        OfferCreateRequest(
            vendor_id=VENDOR,
            store_id=uuid4(),
            sku_id=uuid4(),
            country_code="NP",
            seller_sku="A",
            list_price=Decimal("1"),
            currency_code="NPR",
            quantity=4,
        )


def test_missing_attributes_block_submit() -> None:
    """Require category attributes before review."""
    repo, scope, service, category_id, brand_id = _catalog()
    del scope
    repo.required.add(uuid4())
    product = _product(service, category_id, brand_id)
    with pytest.raises(CatalogServiceError) as missing:
        service.submit_product(_actor(OWNER), product.product_id, VENDOR, ["NP"])
    assert missing.value.code == "ATTRIBUTES_MISSING"
    assert repo.countries == []


def _approved_offer(repo: OfferRepo, scope: Scope, service: CatalogOfferService, category_id, brand_id):
    """Create a draft offer for an approved Nepal product."""
    product = _product(service, category_id, brand_id)
    sku = service.add_sku(_actor(OWNER), product.product_id, VENDOR, "NP", "RED-1", [])
    service.submit_product(_actor(OWNER), product.product_id, VENDOR, ["NP"])
    service.moderate_product(_actor(REVIEWER), product.product_id, "NP", ReviewDecision.APPROVED, "ok")
    nepal = DevelopmentCountryReader().resolve_country("NP").country_id
    store_id = uuid4()
    scope.stores[store_id] = (VENDOR, nepal)
    offer = service.create_offer(
        _actor(OWNER),
        VENDOR,
        store_id,
        sku.sku_id,
        "NP",
        "seller-a",
        Decimal("12.50"),
        "NPR",
    )
    stored = repo.get_offer(offer.offer_id)
    assert stored is not None
    assert stored.status is OfferStatus.DRAFT
    assert repo.products[0].status is ProductStatus.ACTIVE
    return stored
