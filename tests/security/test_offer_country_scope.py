"""Test that offer publication does not leak across vendors or countries."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.auth_context import AuthContext
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.models import OfferCountry, ProductCountry, SellerOffer, Sku
from app.modules.catalog.offers import CatalogOfferService
from app.modules.catalog.types import ModerationStatus, OfferStatus, PublicationStatus
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.grants import AuthGrantError
from app.ports.document_storage import MemoryDocumentStorage

NOW = datetime(2026, 10, 28, tzinfo=UTC)
OWNER = UUID(int=11)
STAFF = UUID(int=12)
VENDOR = UUID(int=13)
OUTSIDER = UUID(int=14)


class Repo:
    """Hold one offer for scope checks."""

    def __init__(self) -> None:
        """Create one draft Nepal offer."""
        self.nepal = DevelopmentCountryReader().resolve_country("NP").country_id
        self.sku = Sku(sku_id=uuid4(), product_id=uuid4(), sku_code="RED")
        self.offer = SellerOffer(
            offer_id=uuid4(),
            vendor_id=VENDOR,
            store_id=uuid4(),
            sku_id=self.sku.sku_id,
            seller_sku="A",
            status=OfferStatus.DRAFT,
            version=1,
        )
        self.row = OfferCountry(
            offer_id=self.offer.offer_id,
            country_id=self.nepal,
            publication_status=PublicationStatus.DRAFT,
            currency_code="NPR",
            list_price=Decimal("9.00"),
        )
        self.product_country = ProductCountry(
            product_id=self.sku.product_id,
            country_id=self.nepal,
            moderation_status=ModerationStatus.PENDING,
        )

    def get_offer(self, offer_id: UUID, *, for_update: bool = False):
        """Return the only offer."""
        del for_update
        if offer_id != self.offer.offer_id:
            return None
        return self.offer

    def get_offer_country(self, offer_id: UUID, country_id: UUID, *, for_update: bool = False):
        """Return the Nepal price row."""
        del for_update
        if offer_id != self.offer.offer_id or country_id != self.nepal:
            return None
        return self.row

    def get_sku(self, sku_id: UUID):
        """Return the shared SKU."""
        if sku_id != self.sku.sku_id:
            return None
        return self.sku

    def get_product(self, product_id: UUID, *, for_update: bool = False):
        """Return a stand-in product."""
        del for_update
        if product_id != self.sku.product_id:
            return None
        return type("ProductRow", (), {"product_id": product_id, "category_id": uuid4(), "status": "ACTIVE"})()

    def get_product_country(self, product_id: UUID, country_id: UUID, *, for_update: bool = False):
        """Return the pending moderation row."""
        del for_update
        if product_id != self.sku.product_id or country_id != self.nepal:
            return None
        return self.product_country

    def is_category_enabled(self, category_id: UUID, country_id: UUID) -> bool:
        """Keep the category enabled."""
        del category_id, country_id
        return True

    def get_category(self, category_id: UUID):
        """Return a parentless category."""
        return type("CategoryRow", (), {"category_id": category_id, "parent_id": None, "status": "ACTIVE"})()


class Authorizer:
    """Allow only the owner to publish."""

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Grant publication to the owner."""
        del country_id
        return (
            actor.user_id == OWNER
            and vendor_id == VENDOR
            and permission == "catalog.offer.publish"
        )

    def allows_global(self, actor: AuthContext, permission: str) -> bool:
        """Deny global grants."""
        del actor, permission
        return False


class Scope:
    """Recognize the owner and staff, but not an outsider."""

    def is_selling_enabled(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Allow selling."""
        del vendor_id, country_id
        return True

    def owns_store(self, vendor_id: UUID, store_id: UUID, country_id: UUID) -> bool:
        """Unused by publication lookup."""
        del vendor_id, store_id, country_id
        return False

    def is_active_member(self, user_id: UUID, vendor_id: UUID) -> bool:
        """Return membership for the owner and staff only."""
        return vendor_id == VENDOR and user_id in {OWNER, STAFF}


class Sellers:
    """Approve the vendor."""

    def is_approved(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return approval."""
        del vendor_id, country_id
        return True


class Consume:
    """Reject a bad step-up token."""

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Raise before publication changes."""
        del actor, action_class
        if token != "ok":
            raise AuthGrantError("bad")


def _actor(user_id: UUID) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())


def test_another_vendors_offer_is_hidden_and_staff_cannot_publish() -> None:
    """Return 404 for a stranger and 403 for staff without publication rights."""
    repo = Repo()
    service = CatalogOfferService(
        repo,
        DevelopmentCountryReader(),
        Authorizer(),
        Sellers(),
        Scope(),
        MemoryDocumentStorage(),
        Consume(),
    )
    with pytest.raises(CatalogServiceError) as hidden:
        service.publish_offer(_actor(OUTSIDER), repo.offer.offer_id, "NP", "ok")
    assert hidden.value.status_code == 404
    with pytest.raises(CatalogServiceError) as staff:
        service.publish_offer(_actor(STAFF), repo.offer.offer_id, "NP", "ok")
    assert staff.value.status_code == 403
    with pytest.raises(AuthGrantError):
        service.publish_offer(_actor(OWNER), repo.offer.offer_id, "NP", "bad")
    assert repo.offer.status is OfferStatus.DRAFT
    assert repo.row.publication_status is PublicationStatus.DRAFT
