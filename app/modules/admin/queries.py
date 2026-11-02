"""Read-only cross-domain queries that power the admin panel.

The marketplace is split into identity, vendor, and catalog stores. The
admin panel needs small, joined views across those stores, so this module
keeps every read in one place and never mutates business data.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.modules.catalog.models import (
    Brand,
    BrandCountry,
    CatalogProduct,
    Category,
    CategoryCountry,
    CategoryTranslation,
    OfferCountry,
    ProductCountry,
    ProductTranslation,
    SellerOffer,
    Sku,
)
from app.modules.catalog.types import (
    ModerationStatus,
    OfferStatus,
    ProductStatus,
    PublicationStatus,
    RecordStatus,
)
from app.modules.identity.models import UserAccount
from app.modules.identity.types import AccountStatus
from app.modules.vendor.models import (
    Store,
    StoreTranslation,
    Vendor,
    VendorCountry,
    VendorDocument,
    VendorMember,
    VendorPayoutAccount,
    Warehouse,
)
from app.modules.vendor.types import (
    AccountType,
    OnboardingStatus,
    StorefrontStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)

_PENDING_ONBOARDING = (OnboardingStatus.SUBMITTED, OnboardingStatus.REVIEW)


@dataclass(frozen=True, slots=True)
class MetricView:
    """Expose one total/active/pending counter triplet."""

    total: int
    active: int
    pending: int


@dataclass(frozen=True, slots=True)
class PendingApplicationRow:
    """Expose one application waiting for review."""

    vendor_id: UUID
    vendor_country_id: UUID
    legal_name: str
    display_name: str
    country_id: UUID
    onboarding_status: OnboardingStatus
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OverviewView:
    """Expose the dashboard headline numbers."""

    customers: MetricView
    vendors: MetricView
    applications: MetricView
    catalog_products: MetricView
    categories: int
    brands: int
    offers: int
    published_offers: int
    pending_applications: list[PendingApplicationRow]


@dataclass(frozen=True, slots=True)
class CountryUsageView:
    """Expose how much marketplace data one country holds."""

    country_id: UUID
    applications: int
    pending_applications: int
    enabled_categories: int
    published_offers: int


@dataclass(frozen=True, slots=True)
class CustomerRow:
    """Expose one customer account."""

    user_id: UUID
    email: str | None
    phone: str | None
    status: AccountStatus
    email_verified: bool
    phone_verified: bool
    vendor_name: str | None
    created_at: datetime
    last_login_at: datetime | None


@dataclass(frozen=True, slots=True)
class CustomerPage:
    """Expose one customer page."""

    items: list[CustomerRow]
    total: int


@dataclass(frozen=True, slots=True)
class VendorCountryRow:
    """Expose one vendor country application."""

    vendor_country_id: UUID
    country_id: UUID
    onboarding_status: OnboardingStatus
    verification_status: VerificationStatus
    selling_enabled: bool
    payout_enabled: bool


@dataclass(frozen=True, slots=True)
class VendorRow:
    """Expose one seller organization."""

    vendor_id: UUID
    legal_name: str
    display_name: str
    vendor_type: VendorType
    status: VendorStatus
    owner_email: str | None
    created_at: datetime
    countries: list[VendorCountryRow]


@dataclass(frozen=True, slots=True)
class VendorPage:
    """Expose one vendor page."""

    items: list[VendorRow]
    total: int


@dataclass(frozen=True, slots=True)
class StoreRow:
    """Expose one seller storefront."""

    store_id: UUID
    name: str | None
    slug: str
    status: StorefrontStatus
    country_id: UUID
    rating: Decimal
    followers_count: int


@dataclass(frozen=True, slots=True)
class WarehouseRow:
    """Expose one seller warehouse."""

    warehouse_id: UUID
    code: str
    country_id: UUID
    city_text: str
    status: StorefrontStatus
    fulfillment_enabled: bool
    pickup_enabled: bool


@dataclass(frozen=True, slots=True)
class PayoutAccountRow:
    """Expose one masked payout destination."""

    vendor_payout_account_id: UUID
    account_type: AccountType
    currency_code: str
    account_number_masked: str
    is_default: bool
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class VendorDetailView:
    """Expose one seller with its operational records."""

    vendor: VendorRow
    stores: list[StoreRow]
    warehouses: list[WarehouseRow]
    payout_accounts: list[PayoutAccountRow]


@dataclass(frozen=True, slots=True)
class DocumentRow:
    """Expose one application document."""

    vendor_document_id: UUID
    document_type: str
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class ApplicationRow:
    """Expose one seller application."""

    vendor_id: UUID
    vendor_country_id: UUID
    country_id: UUID
    legal_name: str
    display_name: str
    vendor_type: VendorType
    vendor_status: VendorStatus
    onboarding_status: OnboardingStatus
    selling_enabled: bool
    payout_enabled: bool
    owner_email: str | None
    created_at: datetime
    documents: list[DocumentRow]


@dataclass(frozen=True, slots=True)
class ApplicationPage:
    """Expose one application page."""

    items: list[ApplicationRow]
    total: int


@dataclass(frozen=True, slots=True)
class CategoryRow:
    """Expose one canonical category."""

    category_id: UUID
    parent_id: UUID | None
    code: str
    name: str | None
    status: RecordStatus
    enabled_country_ids: list[UUID]


@dataclass(frozen=True, slots=True)
class BrandRow:
    """Expose one canonical brand."""

    brand_id: UUID
    name: str
    status: RecordStatus
    enabled_country_ids: list[UUID]


@dataclass(frozen=True, slots=True)
class ProductCountryRow:
    """Expose one product moderation state."""

    country_id: UUID
    moderation_status: ModerationStatus


@dataclass(frozen=True, slots=True)
class CatalogProductRow:
    """Expose one shared catalog product."""

    product_id: UUID
    name: str | None
    category_code: str
    category_name: str | None
    brand_name: str
    status: ProductStatus
    created_by_email: str | None
    created_at: datetime
    sku_count: int
    offer_count: int
    countries: list[ProductCountryRow]


@dataclass(frozen=True, slots=True)
class CatalogProductPage:
    """Expose one catalog product page."""

    items: list[CatalogProductRow]
    total: int


@dataclass(frozen=True, slots=True)
class OfferRow:
    """Expose one seller offer."""

    offer_id: UUID
    seller_sku: str
    product_id: UUID
    product_name: str | None
    store_slug: str
    vendor_name: str
    country_id: UUID
    status: OfferStatus
    publication_status: PublicationStatus
    currency_code: str
    list_price: Decimal
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OfferPage:
    """Expose one offer page."""

    items: list[OfferRow]
    total: int


def get_overview(db: Session) -> OverviewView:
    """Return the dashboard counters and the oldest pending applications."""
    applications = MetricView(
        total=_count(db, VendorCountry),
        active=_count(
            db,
            VendorCountry,
            VendorCountry.onboarding_status == OnboardingStatus.APPROVED,
        ),
        pending=_count(
            db,
            VendorCountry,
            VendorCountry.onboarding_status.in_(_PENDING_ONBOARDING),
        ),
    )
    pending_rows = db.execute(
        select(VendorCountry, Vendor)
        .join(Vendor, Vendor.vendor_id == VendorCountry.vendor_id)
        .where(VendorCountry.onboarding_status.in_(_PENDING_ONBOARDING))
        .order_by(VendorCountry.created_at, VendorCountry.vendor_country_id)
        .limit(5)
    ).all()
    return OverviewView(
        customers=MetricView(
            total=_count(db, UserAccount),
            active=_count(db, UserAccount, UserAccount.status == AccountStatus.ACTIVE),
            pending=_count(db, UserAccount, UserAccount.status == AccountStatus.PENDING),
        ),
        vendors=MetricView(
            total=_count(db, Vendor),
            active=_count(db, Vendor, Vendor.status == VendorStatus.ACTIVE),
            pending=_count(db, Vendor, Vendor.status == VendorStatus.PENDING),
        ),
        applications=applications,
        catalog_products=MetricView(
            total=_count(db, CatalogProduct),
            active=_count(db, CatalogProduct, CatalogProduct.status == ProductStatus.ACTIVE),
            pending=_count(
                db,
                ProductCountry,
                ProductCountry.moderation_status == ModerationStatus.PENDING,
            ),
        ),
        categories=_count(db, Category, Category.status == RecordStatus.ACTIVE),
        brands=_count(db, Brand, Brand.status == RecordStatus.ACTIVE),
        offers=_count(db, SellerOffer),
        published_offers=_count(
            db,
            OfferCountry,
            OfferCountry.publication_status == PublicationStatus.PUBLISHED,
        ),
        pending_applications=[
            PendingApplicationRow(
                vendor_id=country.vendor_id,
                vendor_country_id=country.vendor_country_id,
                legal_name=vendor.legal_name,
                display_name=vendor.display_name,
                country_id=country.country_id,
                onboarding_status=country.onboarding_status,
                created_at=country.created_at,
            )
            for country, vendor in pending_rows
        ],
    )


def list_country_usage(db: Session) -> list[CountryUsageView]:
    """Return every country that already holds marketplace data."""
    country_ids: set[UUID] = set()
    country_ids.update(db.scalars(select(VendorCountry.country_id).distinct()))
    country_ids.update(
        db.scalars(
            select(CategoryCountry.country_id)
            .where(CategoryCountry.enabled.is_(True))
            .distinct()
        )
    )
    country_ids.update(
        db.scalars(
            select(OfferCountry.country_id)
            .where(OfferCountry.publication_status == PublicationStatus.PUBLISHED)
            .distinct()
        )
    )
    usages: list[CountryUsageView] = []
    for country_id in sorted(country_ids):
        usages.append(
            CountryUsageView(
                country_id=country_id,
                applications=_count(db, VendorCountry, VendorCountry.country_id == country_id),
                pending_applications=_count(
                    db,
                    VendorCountry,
                    VendorCountry.country_id == country_id,
                    VendorCountry.onboarding_status.in_(_PENDING_ONBOARDING),
                ),
                enabled_categories=_count(
                    db,
                    CategoryCountry,
                    CategoryCountry.country_id == country_id,
                    CategoryCountry.enabled.is_(True),
                ),
                published_offers=_count(
                    db,
                    OfferCountry,
                    OfferCountry.country_id == country_id,
                    OfferCountry.publication_status == PublicationStatus.PUBLISHED,
                ),
            )
        )
    return usages


def list_customers(
    db: Session,
    *,
    q: str | None,
    status: AccountStatus | None,
    limit: int,
    offset: int,
) -> CustomerPage:
    """Return one filtered customer page."""
    conditions = []
    if q:
        conditions.append(
            or_(
                _contains(UserAccount.email, q),
                _contains(UserAccount.phone_e164, q),
            )
        )
    if status is not None:
        conditions.append(UserAccount.status == status)
    total = _count(db, UserAccount, *conditions)
    accounts = list(
        db.scalars(
            select(UserAccount)
            .where(*conditions)
            .order_by(UserAccount.created_at.desc(), UserAccount.user_id)
            .offset(offset)
            .limit(limit)
        )
    )
    vendor_names = _vendor_names_for(db, [account.user_id for account in accounts])
    return CustomerPage(
        items=[
            CustomerRow(
                user_id=account.user_id,
                email=account.email,
                phone=account.phone_e164,
                status=account.status,
                email_verified=account.email_verified_at is not None,
                phone_verified=account.phone_verified_at is not None,
                vendor_name=vendor_names.get(account.user_id),
                created_at=account.created_at,
                last_login_at=account.last_login_at,
            )
            for account in accounts
        ],
        total=total,
    )


def list_vendors(
    db: Session,
    *,
    q: str | None,
    status: VendorStatus | None,
    country_id: UUID | None,
    limit: int,
    offset: int,
) -> VendorPage:
    """Return one filtered vendor page."""
    statement = select(Vendor)
    if q:
        statement = statement.where(
            or_(
                _contains(Vendor.legal_name, q),
                _contains(Vendor.display_name, q),
            )
        )
    if status is not None:
        statement = statement.where(Vendor.status == status)
    if country_id is not None:
        statement = statement.where(
            Vendor.vendor_id.in_(
                select(VendorCountry.vendor_id).where(
                    VendorCountry.country_id == country_id
                )
            )
        )
    total = _count_statement(db, statement)
    vendors = list(
        db.scalars(
            statement.order_by(Vendor.created_at.desc(), Vendor.vendor_id)
            .offset(offset)
            .limit(limit)
        )
    )
    vendor_ids = [vendor.vendor_id for vendor in vendors]
    countries = _countries_for(db, vendor_ids)
    owners = _owner_emails_for(db, [vendor.created_by_user_id for vendor in vendors])
    return VendorPage(
        items=[
            VendorRow(
                vendor_id=vendor.vendor_id,
                legal_name=vendor.legal_name,
                display_name=vendor.display_name,
                vendor_type=vendor.vendor_type,
                status=vendor.status,
                owner_email=owners.get(vendor.created_by_user_id),
                created_at=vendor.created_at,
                countries=countries.get(vendor.vendor_id, []),
            )
            for vendor in vendors
        ],
        total=total,
    )


def get_vendor_detail(db: Session, vendor_id: UUID) -> VendorDetailView | None:
    """Return one seller with its stores, warehouses, and payout accounts."""
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return None
    owner = db.get(UserAccount, vendor.created_by_user_id)
    countries = _countries_for(db, [vendor_id]).get(vendor_id, [])
    stores = list(
        db.scalars(
            select(Store)
            .where(Store.vendor_id == vendor_id)
            .order_by(Store.created_at, Store.store_id)
        )
    )
    names = _store_names_for(db, [store.store_id for store in stores])
    warehouses = list(
        db.scalars(
            select(Warehouse)
            .where(Warehouse.vendor_id == vendor_id)
            .order_by(Warehouse.code)
        )
    )
    country_row_ids = [row.vendor_country_id for row in countries]
    payouts = []
    if country_row_ids:
        payouts = list(
            db.scalars(
                select(VendorPayoutAccount)
                .where(VendorPayoutAccount.vendor_country_id.in_(country_row_ids))
                .order_by(VendorPayoutAccount.created_at)
            )
        )
    return VendorDetailView(
        vendor=VendorRow(
            vendor_id=vendor.vendor_id,
            legal_name=vendor.legal_name,
            display_name=vendor.display_name,
            vendor_type=vendor.vendor_type,
            status=vendor.status,
            owner_email=None if owner is None else owner.email,
            created_at=vendor.created_at,
            countries=countries,
        ),
        stores=[
            StoreRow(
                store_id=store.store_id,
                name=names.get(store.store_id),
                slug=store.slug,
                status=store.status,
                country_id=store.country_id,
                rating=store.rating,
                followers_count=store.followers_count,
            )
            for store in stores
        ],
        warehouses=[
            WarehouseRow(
                warehouse_id=warehouse.warehouse_id,
                code=warehouse.code,
                country_id=warehouse.country_id,
                city_text=warehouse.city_text,
                status=warehouse.status,
                fulfillment_enabled=warehouse.fulfillment_enabled,
                pickup_enabled=warehouse.pickup_enabled,
            )
            for warehouse in warehouses
        ],
        payout_accounts=[
            PayoutAccountRow(
                vendor_payout_account_id=payout.vendor_payout_account_id,
                account_type=payout.account_type,
                currency_code=payout.currency_code,
                account_number_masked=payout.account_number_masked,
                is_default=payout.is_default,
                verification_status=payout.verification_status,
            )
            for payout in payouts
        ],
    )


def list_applications(
    db: Session,
    *,
    country_id: UUID | None,
    status: OnboardingStatus | None,
    q: str | None,
    limit: int,
    offset: int,
) -> ApplicationPage:
    """Return one filtered seller application page."""
    statement = (
        select(VendorCountry, Vendor, UserAccount.email)
        .join(Vendor, Vendor.vendor_id == VendorCountry.vendor_id)
        .join(UserAccount, UserAccount.user_id == Vendor.created_by_user_id)
    )
    if country_id is not None:
        statement = statement.where(VendorCountry.country_id == country_id)
    if status is not None:
        statement = statement.where(VendorCountry.onboarding_status == status)
    if q:
        statement = statement.where(
            or_(
                _contains(Vendor.legal_name, q),
                _contains(Vendor.display_name, q),
                _contains(UserAccount.email, q),
            )
        )
    total = _count_statement(db, statement)
    rows = db.execute(
        statement.order_by(
            VendorCountry.created_at.desc(),
            VendorCountry.vendor_country_id,
        )
        .offset(offset)
        .limit(limit)
    ).all()
    documents = _documents_for(
        db,
        [country.vendor_country_id for country, _, _ in rows],
    )
    return ApplicationPage(
        items=[
            ApplicationRow(
                vendor_id=vendor.vendor_id,
                vendor_country_id=country.vendor_country_id,
                country_id=country.country_id,
                legal_name=vendor.legal_name,
                display_name=vendor.display_name,
                vendor_type=vendor.vendor_type,
                vendor_status=vendor.status,
                onboarding_status=country.onboarding_status,
                selling_enabled=country.selling_enabled,
                payout_enabled=country.payout_enabled,
                owner_email=email,
                created_at=country.created_at,
                documents=documents.get(country.vendor_country_id, []),
            )
            for country, vendor, email in rows
        ],
        total=total,
    )


def list_categories(db: Session, locale_code: str) -> list[CategoryRow]:
    """Return every canonical category with its enabled countries."""
    rows = db.execute(
        select(Category, CategoryTranslation.name)
        .outerjoin(
            CategoryTranslation,
            and_(
                CategoryTranslation.category_id == Category.category_id,
                CategoryTranslation.locale_code == locale_code,
            ),
        )
        .order_by(Category.code)
    ).all()
    enabled = _enabled_countries_for(
        db,
        CategoryCountry,
        "category_id",
        [category.category_id for category, _ in rows],
    )
    return [
        CategoryRow(
            category_id=category.category_id,
            parent_id=category.parent_id,
            code=category.code,
            name=name,
            status=category.status,
            enabled_country_ids=enabled.get(category.category_id, []),
        )
        for category, name in rows
    ]


def list_brands(db: Session) -> list[BrandRow]:
    """Return every canonical brand with its enabled countries."""
    brands = list(db.scalars(select(Brand).order_by(Brand.name)))
    enabled = _enabled_countries_for(
        db,
        BrandCountry,
        "brand_id",
        [brand.brand_id for brand in brands],
    )
    return [
        BrandRow(
            brand_id=brand.brand_id,
            name=brand.name,
            status=brand.status,
            enabled_country_ids=enabled.get(brand.brand_id, []),
        )
        for brand in brands
    ]


def list_catalog_products(
    db: Session,
    *,
    q: str | None,
    status: ProductStatus | None,
    country_id: UUID | None,
    locale_code: str,
    limit: int,
    offset: int,
) -> CatalogProductPage:
    """Return one filtered shared catalog product page."""
    statement = (
        select(
            CatalogProduct,
            ProductTranslation.name,
            Category.code,
            CategoryTranslation.name,
            Brand.name,
            UserAccount.email,
        )
        .join(Category, Category.category_id == CatalogProduct.category_id)
        .join(Brand, Brand.brand_id == CatalogProduct.brand_id)
        .outerjoin(
            ProductTranslation,
            and_(
                ProductTranslation.product_id == CatalogProduct.product_id,
                ProductTranslation.locale_code == locale_code,
            ),
        )
        .outerjoin(
            CategoryTranslation,
            and_(
                CategoryTranslation.category_id == Category.category_id,
                CategoryTranslation.locale_code == locale_code,
            ),
        )
        .outerjoin(UserAccount, UserAccount.user_id == CatalogProduct.created_by)
    )
    if status is not None:
        statement = statement.where(CatalogProduct.status == status)
    if country_id is not None:
        statement = statement.join(
            ProductCountry,
            and_(
                ProductCountry.product_id == CatalogProduct.product_id,
                ProductCountry.country_id == country_id,
            ),
        )
    if q:
        statement = statement.where(
            or_(
                CatalogProduct.product_id.in_(
                    select(ProductTranslation.product_id).where(
                        _contains(ProductTranslation.name, q)
                    )
                ),
                _contains(Category.code, q),
                _contains(Brand.name, q),
            )
        )
    total = _count_statement(db, statement)
    rows = db.execute(
        statement.order_by(
            CatalogProduct.created_at.desc(),
            CatalogProduct.product_id,
        )
        .offset(offset)
        .limit(limit)
    ).all()
    product_ids = [product.product_id for product, *_ in rows]
    countries = _product_countries_for(db, product_ids)
    sku_counts = _grouped_counts(db, Sku.product_id, Sku.sku_id, product_ids)
    offer_counts = _offer_counts_for(db, product_ids)
    return CatalogProductPage(
        items=[
            CatalogProductRow(
                product_id=product.product_id,
                name=name,
                category_code=category_code,
                category_name=category_name,
                brand_name=brand_name,
                status=product.status,
                created_by_email=email,
                created_at=product.created_at,
                sku_count=sku_counts.get(product.product_id, 0),
                offer_count=offer_counts.get(product.product_id, 0),
                countries=countries.get(product.product_id, []),
            )
            for product, name, category_code, category_name, brand_name, email in rows
        ],
        total=total,
    )


def list_offers(
    db: Session,
    *,
    q: str | None,
    country_id: UUID | None,
    publication_status: PublicationStatus | None,
    locale_code: str,
    limit: int,
    offset: int,
) -> OfferPage:
    """Return one filtered seller offer page."""
    statement = (
        select(
            SellerOffer,
            OfferCountry,
            Sku.sku_code,
            CatalogProduct.product_id,
            ProductTranslation.name,
            Store.slug,
            Vendor.display_name,
        )
        .join(OfferCountry, OfferCountry.offer_id == SellerOffer.offer_id)
        .join(Sku, Sku.sku_id == SellerOffer.sku_id)
        .join(CatalogProduct, CatalogProduct.product_id == Sku.product_id)
        .join(Store, Store.store_id == SellerOffer.store_id)
        .join(Vendor, Vendor.vendor_id == SellerOffer.vendor_id)
        .outerjoin(
            ProductTranslation,
            and_(
                ProductTranslation.product_id == CatalogProduct.product_id,
                ProductTranslation.locale_code == locale_code,
            ),
        )
    )
    if country_id is not None:
        statement = statement.where(OfferCountry.country_id == country_id)
    if publication_status is not None:
        statement = statement.where(
            OfferCountry.publication_status == publication_status
        )
    if q:
        statement = statement.where(
            or_(
                _contains(SellerOffer.seller_sku, q),
                _contains(Store.slug, q),
                _contains(Vendor.display_name, q),
                CatalogProduct.product_id.in_(
                    select(ProductTranslation.product_id).where(
                        _contains(ProductTranslation.name, q)
                    )
                ),
            )
        )
    total = _count_statement(db, statement)
    rows = db.execute(
        statement.order_by(SellerOffer.updated_at.desc(), SellerOffer.offer_id)
        .offset(offset)
        .limit(limit)
    ).all()
    return OfferPage(
        items=[
            OfferRow(
                offer_id=offer.offer_id,
                seller_sku=seller_sku,
                product_id=product_id,
                product_name=product_name,
                store_slug=store_slug,
                vendor_name=vendor_name,
                country_id=country.country_id,
                status=offer.status,
                publication_status=country.publication_status,
                currency_code=country.currency_code,
                list_price=country.list_price,
                updated_at=offer.updated_at,
            )
            for offer, country, seller_sku, product_id, product_name, store_slug, vendor_name in rows
        ],
        total=total,
    )


def _count(db: Session, model, *conditions) -> int:
    """Count rows of one model matching the supplied conditions."""
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(db.scalar(statement) or 0)


def _count_statement(db: Session, statement) -> int:
    """Count the rows a select statement would return."""
    return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _contains(column, value: str):
    """Build a case-insensitive contains condition with escaped wildcards."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _vendor_names_for(db: Session, user_ids: list[UUID]) -> dict[UUID, str]:
    """Map account identifiers to the vendor name they belong to."""
    if not user_ids:
        return {}
    names: dict[UUID, str] = {}
    for user_id, display_name in db.execute(
        select(Vendor.created_by_user_id, Vendor.display_name).where(
            Vendor.created_by_user_id.in_(user_ids)
        )
    ).all():
        names.setdefault(user_id, display_name)
    for user_id, display_name in db.execute(
        select(VendorMember.user_id, Vendor.display_name)
        .join(Vendor, Vendor.vendor_id == VendorMember.vendor_id)
        .where(VendorMember.user_id.in_(user_ids))
    ).all():
        names.setdefault(user_id, display_name)
    return names


def _owner_emails_for(db: Session, user_ids: list[UUID]) -> dict[UUID, str | None]:
    """Map account identifiers to their email address."""
    if not user_ids:
        return {}
    return {
        user_id: email
        for user_id, email in db.execute(
            select(UserAccount.user_id, UserAccount.email).where(
                UserAccount.user_id.in_(user_ids)
            )
        ).all()
    }


def _countries_for(
    db: Session,
    vendor_ids: list[UUID],
) -> dict[UUID, list[VendorCountryRow]]:
    """Group country applications by vendor."""
    if not vendor_ids:
        return {}
    grouped: dict[UUID, list[VendorCountryRow]] = {}
    for country in db.scalars(
        select(VendorCountry)
        .where(VendorCountry.vendor_id.in_(vendor_ids))
        .order_by(VendorCountry.created_at, VendorCountry.vendor_country_id)
    ):
        grouped.setdefault(country.vendor_id, []).append(
            VendorCountryRow(
                vendor_country_id=country.vendor_country_id,
                country_id=country.country_id,
                onboarding_status=country.onboarding_status,
                verification_status=country.verification_status,
                selling_enabled=country.selling_enabled,
                payout_enabled=country.payout_enabled,
            )
        )
    return grouped


def _store_names_for(db: Session, store_ids: list[UUID]) -> dict[UUID, str]:
    """Map store identifiers to the first available translation name."""
    if not store_ids:
        return {}
    names: dict[UUID, str] = {}
    for translation in db.scalars(
        select(StoreTranslation)
        .where(StoreTranslation.store_id.in_(store_ids))
        .order_by(StoreTranslation.locale_code)
    ):
        names.setdefault(translation.store_id, translation.name)
    return names


def _documents_for(
    db: Session,
    vendor_country_ids: list[UUID],
) -> dict[UUID, list[DocumentRow]]:
    """Group application documents by country application."""
    if not vendor_country_ids:
        return {}
    grouped: dict[UUID, list[DocumentRow]] = {}
    for document in db.scalars(
        select(VendorDocument)
        .where(VendorDocument.vendor_country_id.in_(vendor_country_ids))
        .order_by(VendorDocument.created_at)
    ):
        grouped.setdefault(document.vendor_country_id, []).append(
            DocumentRow(
                vendor_document_id=document.vendor_document_id,
                document_type=document.document_type.value,
                verification_status=document.verification_status,
            )
        )
    return grouped


def _enabled_countries_for(
    db: Session,
    model,
    id_name: str,
    record_ids: list[UUID],
) -> dict[UUID, list[UUID]]:
    """Group enabled country identifiers by catalog record."""
    if not record_ids:
        return {}
    grouped: dict[UUID, list[UUID]] = {}
    for record_id, country_id in db.execute(
        select(getattr(model, id_name), model.country_id).where(
            getattr(model, id_name).in_(record_ids),
            model.enabled.is_(True),
        )
    ).all():
        grouped.setdefault(record_id, []).append(country_id)
    return grouped


def _product_countries_for(
    db: Session,
    product_ids: list[UUID],
) -> dict[UUID, list[ProductCountryRow]]:
    """Group moderation states by product."""
    if not product_ids:
        return {}
    grouped: dict[UUID, list[ProductCountryRow]] = {}
    for row in db.scalars(
        select(ProductCountry)
        .where(ProductCountry.product_id.in_(product_ids))
        .order_by(ProductCountry.country_id)
    ):
        grouped.setdefault(row.product_id, []).append(
            ProductCountryRow(
                country_id=row.country_id,
                moderation_status=row.moderation_status,
            )
        )
    return grouped


def _grouped_counts(
    db: Session,
    group_column,
    count_column,
    values: list[UUID],
) -> dict[UUID, int]:
    """Count rows per group for a bounded list of identifiers."""
    if not values:
        return {}
    return {
        group_id: int(total)
        for group_id, total in db.execute(
            select(group_column, func.count(count_column))
            .where(group_column.in_(values))
            .group_by(group_column)
        ).all()
    }


def _offer_counts_for(db: Session, product_ids: list[UUID]) -> dict[UUID, int]:
    """Count seller offers per product."""
    if not product_ids:
        return {}
    return {
        product_id: int(total)
        for product_id, total in db.execute(
            select(Sku.product_id, func.count(SellerOffer.offer_id))
            .join(SellerOffer, SellerOffer.sku_id == Sku.sku_id)
            .where(Sku.product_id.in_(product_ids))
            .group_by(Sku.product_id)
        ).all()
    }
