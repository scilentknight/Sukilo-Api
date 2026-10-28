"""Test product, SKU, media, and offer persistence on PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.auth_context import AuthContext
from app.modules.access.catalog import ROLES
from app.modules.access.models import UserRoleAssignment
from app.modules.access.types import ScopeType
from app.modules.access.vendor_grants import AssignmentVendorAuthorizer
from app.modules.catalog.dependencies import VendorApprovedSellerLookup
from app.modules.catalog.models import (
    Brand,
    BrandCountry,
    Category,
    CategoryCountry,
    MediaAsset,
    SellerOffer,
)
from app.modules.catalog.offers import CatalogOfferService
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.types import RecordStatus, ReviewDecision
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.types import AccountStatus
from app.modules.vendor.models import Store, VendorCountry, VendorMember
from app.modules.vendor.types import MemberStatus, OnboardingStatus, StorefrontStatus
from app.ports.document_storage import MemoryDocumentStorage
from app.ports.vendor_scope import SessionVendorScopeReader
from tests.integration.vendor.test_vendor_repository import (
    _alembic,
    _create_temporary_database,
    _drop_database,
    _insert_user,
    _insert_vendor,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)


class Consume:
    """Accept the offer step-up token."""

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Record that a grant was presented."""
        del token, actor, action_class


@pytest.fixture(scope="module")
def offer_database_url() -> Iterator[str]:
    """Upgrade an empty database through the offer permission seed."""
    database_url = _create_temporary_database()
    if database_url is None:
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        _alembic(database_url, "upgrade", "20261028_0041")
        yield database_url
    finally:
        _drop_database(database_url)


@pytest.fixture()
def session(offer_database_url: str) -> Iterator[Session]:
    """Yield a transaction that rolls back after each offer test."""
    engine = create_engine(offer_database_url, poolclass=NullPool)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def test_two_vendors_publish_one_sku_without_stock_tables(session: Session) -> None:
    """Keep one product, two offers, and leave the legacy catalog untouched."""
    countries = DevelopmentCountryReader()
    nepal = countries.resolve_country("NP").country_id
    owner_id = _insert_user(session)
    other_id = _insert_user(session)
    reviewer_id = _insert_user(session)
    for user_id in (owner_id, other_id, reviewer_id):
        _activate(session, user_id)
    vendor = _insert_vendor(session, owner_id)
    other = _insert_vendor(session, other_id)
    _approved_country(session, vendor.vendor_id, nepal, selling=True)
    _approved_country(session, other.vendor_id, nepal, selling=True)
    _member(session, vendor.vendor_id, owner_id)
    _member(session, other.vendor_id, other_id)
    _assign(session, owner_id, "VENDOR_OWNER", nepal, vendor.vendor_id)
    _assign(session, other_id, "VENDOR_OWNER", nepal, other.vendor_id)
    _assign(session, reviewer_id, "COUNTRY_OPERATIONS", nepal, None)
    category_id, brand_id = _taxonomy(session, nepal)
    service = _service(session)
    product = service.create_product(
        _actor(owner_id),
        vendor.vendor_id,
        "NP",
        category_id,
        brand_id,
        "ne",
        "Runner",
        None,
        [],
    )
    sku = service.add_sku(_actor(owner_id), product.product_id, vendor.vendor_id, "NP", "RED-1", [])
    service.submit_product(_actor(owner_id), product.product_id, vendor.vendor_id, ["NP"])
    service.moderate_product(
        _actor(reviewer_id),
        product.product_id,
        "NP",
        ReviewDecision.APPROVED,
        "ok",
    )
    _publish_pair(session, service, owner_id, other_id, vendor.vendor_id, other.vendor_id, nepal, sku.sku_id)
    session.flush()
    assert _count(session, "catalog.product") == 1
    assert _count(session, "catalog.seller_offer") == 2
    assert session.scalar(text("SELECT count(*) FROM public.products")) == 0
    assert service.list_offers("AE", "en") == []
    assert len(service.list_offers("NP", "ne")) == 2
    _assert_no_commerce_tables(session)
    _logo_can_reference_media(session, vendor.vendor_id, owner_id)


def _publish_pair(session, service, owner_id, other_id, vendor_id, other_vendor_id, nepal, sku_id) -> None:
    """Publish the same SKU from two stores."""
    first = _store(session, vendor_id, nepal, "shop-a")
    second = _store(session, other_vendor_id, nepal, "shop-b")
    for actor_id, vendor, store_id, seller_sku in (
        (owner_id, vendor_id, first, "A-1"),
        (other_id, other_vendor_id, second, "B-1"),
    ):
        offer = service.create_offer(
            _actor(actor_id),
            vendor,
            store_id,
            sku_id,
            "NP",
            seller_sku,
            Decimal("25.00"),
            "NPR",
        )
        service.publish_offer(_actor(actor_id), offer.offer_id, "NP", "ok")


def _logo_can_reference_media(session: Session, vendor_id, user_id) -> None:
    """Point a store logo at a real media row."""
    media = MediaAsset(
        media_id=uuid4(),
        storage_key="opaque-key",
        media_type="image/png",
        vendor_id=vendor_id,
        created_by=user_id,
    )
    session.add(media)
    session.flush()
    store = session.scalar(select(Store).limit(1))
    store.logo_media_id = media.media_id
    session.flush()
    assert store.logo_media_id == media.media_id


def _assert_no_commerce_tables(session: Session) -> None:
    """Confirm inventory, cart, and payment tables were not created."""
    count = session.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('cart', 'cart_line', 'payment', 'warehouse_stock', 'stock_ledger') "
            "OR table_schema IN ('inventory', 'commerce', 'payment')"
        )
    )
    assert count == 0
    assert session.scalar(select(SellerOffer).limit(1)) is not None


def _taxonomy(session: Session, country_id) -> tuple:
    """Insert one enabled category and brand."""
    category = Category(category_id=uuid4(), code="shoes", status=RecordStatus.ACTIVE)
    brand = Brand(brand_id=uuid4(), name="Nike", normalized_name="nike", status=RecordStatus.ACTIVE)
    session.add(category)
    session.add(brand)
    session.flush()
    session.add(CategoryCountry(category_id=category.category_id, country_id=country_id, enabled=True))
    session.add(BrandCountry(brand_id=brand.brand_id, country_id=country_id, enabled=True))
    session.flush()
    return category.category_id, brand.brand_id


def _store(session: Session, vendor_id, country_id, slug: str):
    """Insert one store for a vendor country."""
    store = Store(
        store_id=uuid4(),
        vendor_id=vendor_id,
        country_id=country_id,
        slug=slug,
        status=StorefrontStatus.DRAFT,
        rating=Decimal("0"),
        rating_count=0,
        followers_count=0,
        version=1,
    )
    session.add(store)
    session.flush()
    return store.store_id


def _count(session: Session, table_name: str) -> int:
    """Return a row count for one schema-qualified table."""
    return int(session.scalar(text(f"SELECT count(*) FROM {table_name}")))


def _activate(db: Session, user_id) -> None:
    """Mark one inserted account active."""
    from app.modules.identity.models import UserAccount
    account = db.scalar(select(UserAccount).where(UserAccount.user_id == user_id))
    account.status = AccountStatus.ACTIVE
    db.flush()


def _approved_country(db: Session, vendor_id, country_id, *, selling: bool) -> None:
    """Insert one approved country application."""
    db.add(
        VendorCountry(
            vendor_country_id=uuid4(),
            vendor_id=vendor_id,
            country_id=country_id,
            onboarding_status=OnboardingStatus.APPROVED,
            selling_enabled=selling,
            payout_enabled=False,
            version=1,
        )
    )
    db.flush()


def _member(db: Session, vendor_id, user_id) -> None:
    """Insert an active vendor member."""
    db.add(
        VendorMember(
            vendor_member_id=uuid4(),
            vendor_id=vendor_id,
            user_id=user_id,
            status=MemberStatus.ACTIVE,
            accepted_at=NOW,
        )
    )
    db.flush()


def _assign(db: Session, user_id, role_code: str, country_id, vendor_id) -> None:
    """Grant one seeded role."""
    role = next(item for item in ROLES if item.code == role_code)
    if vendor_id is not None:
        scope_type = ScopeType.VENDOR
        scope_id = vendor_id
    elif country_id is not None:
        scope_type = ScopeType.COUNTRY
        scope_id = country_id
    else:
        scope_type = ScopeType.GLOBAL
        scope_id = None
    db.add(
        UserRoleAssignment(
            assignment_id=uuid4(),
            user_id=user_id,
            role_id=role.role_id,
            scope_type=scope_type,
            scope_id=scope_id,
            country_id=country_id,
            vendor_id=vendor_id,
            created_by=user_id,
            version=1,
        )
    )
    db.flush()


def _service(db: Session) -> CatalogOfferService:
    """Build an offer service over the live session."""
    return CatalogOfferService(
        CatalogRepository(db),
        DevelopmentCountryReader(),
        AssignmentVendorAuthorizer(db, lambda: NOW),
        VendorApprovedSellerLookup(db),
        SessionVendorScopeReader(db),
        MemoryDocumentStorage(),
        Consume(),
    )


def _actor(user_id) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())
