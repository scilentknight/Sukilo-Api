"""Expose read-only administration routes for the admin panel."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.application import AccessApplication
from app.modules.access.dependencies import (
    get_access_application,
    require_authenticated,
)
from app.modules.admin import queries
from app.modules.admin.schemas import (
    AdminCountryResponse,
    AdminOverviewResponse,
    ApplicationListResponse,
    ApplicationResponse,
    BrandResponse,
    CatalogProductListResponse,
    CatalogProductResponse,
    CategoryResponse,
    CustomerListResponse,
    CustomerResponse,
    DocumentSummaryResponse,
    MetricResponse,
    OfferListResponse,
    OfferResponse,
    PayoutAccountResponse,
    PendingApplicationSummary,
    ProductCountryResponse,
    StoreResponse,
    VendorCountryResponse,
    VendorDetailResponse,
    VendorListResponse,
    VendorResponse,
    WarehouseResponse,
)
from app.modules.admin.queries import (
    ApplicationRow,
    CatalogProductRow,
    CustomerRow,
    OfferRow,
    VendorCountryRow,
    VendorRow,
)
from app.modules.catalog.types import (
    ProductStatus,
    PublicationStatus,
)
from app.modules.customers.profile import mask_email, mask_phone
from app.modules.identity.types import AccountStatus
from app.modules.vendor.types import OnboardingStatus, VendorStatus
from app.ports.country import CountryReader

router = APIRouter(prefix="/admin", tags=["Admin"])

ADMIN_ROLE_CODES = frozenset(
    {
        "SUPER_ADMIN",
        "COUNTRY_ADMIN",
        "COUNTRY_FINANCE",
        "COUNTRY_OPERATIONS",
        "MODERATOR_SUPPORT",
    }
)


def require_admin(
    context: Annotated[AuthContext, Depends(require_authenticated)],
    application: Annotated[AccessApplication, Depends(get_access_application)],
) -> AuthContext:
    """Allow any marketplace administrator role and no one else."""
    roles = set(application.assignments.list_own_role_codes(context))
    if not roles.intersection(ADMIN_ROLE_CODES):
        raise HTTPException(status_code=403, detail="Access is denied.")
    return context


def get_countries(request: Request) -> CountryReader:
    """Return the process country reader used to resolve market codes."""
    builder = getattr(request.app.state, "access_application_factory", None)
    if builder is None:
        raise HTTPException(status_code=503, detail="Admin services are unavailable.")
    return builder.countries


@router.get("/overview", response_model=AdminOverviewResponse)
def read_overview(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminOverviewResponse:
    """Return dashboard counters and the oldest pending applications."""
    del context
    overview = queries.get_overview(db)
    return AdminOverviewResponse(
        customers=_metric(overview.customers),
        vendors=_metric(overview.vendors),
        applications=_metric(overview.applications),
        catalog_products=_metric(overview.catalog_products),
        categories=overview.categories,
        brands=overview.brands,
        offers=overview.offers,
        published_offers=overview.published_offers,
        pending_applications=[
            PendingApplicationSummary(
                vendor_id=row.vendor_id,
                vendor_country_id=row.vendor_country_id,
                legal_name=row.legal_name,
                display_name=row.display_name,
                country_id=row.country_id,
                country_code=_country_code(countries, row.country_id),
                onboarding_status=row.onboarding_status,
                created_at=row.created_at,
            )
            for row in overview.pending_applications
        ],
    )


@router.get("/countries", response_model=list[AdminCountryResponse])
def list_countries(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AdminCountryResponse]:
    """Return every country that already holds marketplace data."""
    del context
    responses = []
    for usage in queries.list_country_usage(db):
        code = _country_code(countries, usage.country_id)
        if code is None:
            continue
        responses.append(
            AdminCountryResponse(
                country_id=usage.country_id,
                code=code,
                applications=usage.applications,
                pending_applications=usage.pending_applications,
                enabled_categories=usage.enabled_categories,
                published_offers=usage.published_offers,
            )
        )
    return sorted(responses, key=lambda item: item.code)


@router.get("/customers", response_model=CustomerListResponse)
def list_customers(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=160)] = None,
    account_status: Annotated[AccountStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerListResponse:
    """Return one filtered customer page."""
    del context
    page = queries.list_customers(
        db,
        q=q,
        status=account_status,
        limit=limit,
        offset=offset,
    )
    return CustomerListResponse(
        items=[_customer_response(row) for row in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/vendors", response_model=VendorListResponse)
def list_vendors(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=160)] = None,
    vendor_status: Annotated[VendorStatus | None, Query()] = None,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VendorListResponse:
    """Return one filtered vendor page."""
    del context
    country_id = _country_id(countries, country_code)
    page = queries.list_vendors(
        db,
        q=q,
        status=vendor_status,
        country_id=country_id,
        limit=limit,
        offset=offset,
    )
    return VendorListResponse(
        items=[_vendor_response(row, countries) for row in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/vendors/{vendor_id}", response_model=VendorDetailResponse)
def read_vendor(
    vendor_id: UUID,
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
) -> VendorDetailResponse:
    """Return one seller with its operational records."""
    del context
    detail = queries.get_vendor_detail(db, vendor_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Vendor was not found.")
    base = _vendor_response(detail.vendor, countries)
    return VendorDetailResponse(
        **base.model_dump(),
        stores=[
            StoreResponse(
                store_id=store.store_id,
                name=store.name,
                slug=store.slug,
                status=store.status,
                country_id=store.country_id,
                country_code=_country_code(countries, store.country_id),
                rating=store.rating,
                followers_count=store.followers_count,
            )
            for store in detail.stores
        ],
        warehouses=[
            WarehouseResponse(
                warehouse_id=warehouse.warehouse_id,
                code=warehouse.code,
                country_id=warehouse.country_id,
                country_code=_country_code(countries, warehouse.country_id),
                city_text=warehouse.city_text,
                status=warehouse.status,
                fulfillment_enabled=warehouse.fulfillment_enabled,
                pickup_enabled=warehouse.pickup_enabled,
            )
            for warehouse in detail.warehouses
        ],
        payout_accounts=[
            PayoutAccountResponse(
                vendor_payout_account_id=payout.vendor_payout_account_id,
                account_type=payout.account_type,
                currency_code=payout.currency_code,
                account_number_masked=payout.account_number_masked,
                is_default=payout.is_default,
                verification_status=payout.verification_status,
            )
            for payout in detail.payout_accounts
        ],
    )


@router.get("/vendor-applications", response_model=ApplicationListResponse)
def list_vendor_applications(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=160)] = None,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    onboarding_status: Annotated[OnboardingStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApplicationListResponse:
    """Return every seller application for the review workspace."""
    del context
    page = queries.list_applications(
        db,
        country_id=_country_id(countries, country_code),
        status=onboarding_status,
        q=q,
        limit=limit,
        offset=offset,
    )
    return ApplicationListResponse(
        items=[_application_response(row, countries) for row in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/catalog/categories", response_model=list[CategoryResponse])
def list_catalog_categories(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
    locale_code: Annotated[str, Query(min_length=2, max_length=16)] = "en",
) -> list[CategoryResponse]:
    """Return every canonical category, including disabled ones."""
    del context
    return [
        CategoryResponse(
            category_id=row.category_id,
            parent_id=row.parent_id,
            code=row.code,
            name=row.name,
            status=row.status,
            enabled_countries=[
                code
                for code in (
                    _country_code(countries, country_id)
                    for country_id in row.enabled_country_ids
                )
                if code is not None
            ],
        )
        for row in queries.list_categories(db, locale_code)
    ]


@router.get("/catalog/brands", response_model=list[BrandResponse])
def list_catalog_brands(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
) -> list[BrandResponse]:
    """Return every canonical brand, including disabled ones."""
    del context
    return [
        BrandResponse(
            brand_id=row.brand_id,
            name=row.name,
            status=row.status,
            enabled_countries=[
                code
                for code in (
                    _country_code(countries, country_id)
                    for country_id in row.enabled_country_ids
                )
                if code is not None
            ],
        )
        for row in queries.list_brands(db)
    ]


@router.get("/catalog/products", response_model=CatalogProductListResponse)
def list_catalog_products(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=160)] = None,
    product_status: Annotated[ProductStatus | None, Query()] = None,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    locale_code: Annotated[str, Query(min_length=2, max_length=16)] = "en",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CatalogProductListResponse:
    """Return one filtered shared catalog product page."""
    del context
    page = queries.list_catalog_products(
        db,
        q=q,
        status=product_status,
        country_id=_country_id(countries, country_code),
        locale_code=locale_code,
        limit=limit,
        offset=offset,
    )
    return CatalogProductListResponse(
        items=[_product_response(row, countries) for row in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/catalog/offers", response_model=OfferListResponse)
def list_catalog_offers(
    context: Annotated[AuthContext, Depends(require_admin)],
    countries: Annotated[CountryReader, Depends(get_countries)],
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=160)] = None,
    country_code: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    publication_status: Annotated[PublicationStatus | None, Query()] = None,
    locale_code: Annotated[str, Query(min_length=2, max_length=16)] = "en",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OfferListResponse:
    """Return one filtered seller offer page."""
    del context
    page = queries.list_offers(
        db,
        q=q,
        country_id=_country_id(countries, country_code),
        publication_status=publication_status,
        locale_code=locale_code,
        limit=limit,
        offset=offset,
    )
    return OfferListResponse(
        items=[_offer_response(row, countries) for row in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


def _metric(value) -> MetricResponse:
    """Map a counter triplet onto the HTTP contract."""
    return MetricResponse(total=value.total, active=value.active, pending=value.pending)


def _customer_response(row: CustomerRow) -> CustomerResponse:
    """Map a customer row onto the HTTP contract."""
    return CustomerResponse(
        user_id=row.user_id,
        email_masked=mask_email(row.email) if row.email else None,
        phone_masked=mask_phone(row.phone) if row.phone else None,
        status=row.status,
        email_verified=row.email_verified,
        phone_verified=row.phone_verified,
        is_vendor=row.vendor_name is not None,
        vendor_name=row.vendor_name,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


def _vendor_country_response(
    row: VendorCountryRow,
    countries: CountryReader,
) -> VendorCountryResponse:
    """Map a vendor country row onto the HTTP contract."""
    return VendorCountryResponse(
        vendor_country_id=row.vendor_country_id,
        country_id=row.country_id,
        country_code=_country_code(countries, row.country_id),
        onboarding_status=row.onboarding_status,
        verification_status=row.verification_status,
        selling_enabled=row.selling_enabled,
        payout_enabled=row.payout_enabled,
    )


def _vendor_response(row: VendorRow, countries: CountryReader) -> VendorResponse:
    """Map a vendor row onto the HTTP contract."""
    return VendorResponse(
        vendor_id=row.vendor_id,
        legal_name=row.legal_name,
        display_name=row.display_name,
        vendor_type=row.vendor_type,
        status=row.status,
        owner_email_masked=mask_email(row.owner_email) if row.owner_email else None,
        created_at=row.created_at,
        countries=[
            _vendor_country_response(country, countries) for country in row.countries
        ],
    )


def _application_response(
    row: ApplicationRow,
    countries: CountryReader,
) -> ApplicationResponse:
    """Map an application row onto the HTTP contract."""
    return ApplicationResponse(
        vendor_id=row.vendor_id,
        vendor_country_id=row.vendor_country_id,
        country_id=row.country_id,
        country_code=_country_code(countries, row.country_id),
        legal_name=row.legal_name,
        display_name=row.display_name,
        vendor_type=row.vendor_type,
        vendor_status=row.vendor_status,
        onboarding_status=row.onboarding_status,
        selling_enabled=row.selling_enabled,
        payout_enabled=row.payout_enabled,
        owner_email_masked=mask_email(row.owner_email) if row.owner_email else None,
        created_at=row.created_at,
        documents=[
            DocumentSummaryResponse(
                vendor_document_id=document.vendor_document_id,
                document_type=document.document_type,
                verification_status=document.verification_status,
            )
            for document in row.documents
        ],
    )


def _product_response(
    row: CatalogProductRow,
    countries: CountryReader,
) -> CatalogProductResponse:
    """Map a catalog product row onto the HTTP contract."""
    return CatalogProductResponse(
        product_id=row.product_id,
        name=row.name,
        category_code=row.category_code,
        category_name=row.category_name,
        brand_name=row.brand_name,
        status=row.status,
        created_by_email_masked=(
            mask_email(row.created_by_email) if row.created_by_email else None
        ),
        created_at=row.created_at,
        sku_count=row.sku_count,
        offer_count=row.offer_count,
        countries=[
            ProductCountryResponse(
                country_id=country.country_id,
                country_code=_country_code(countries, country.country_id),
                moderation_status=country.moderation_status,
            )
            for country in row.countries
        ],
    )


def _offer_response(row: OfferRow, countries: CountryReader) -> OfferResponse:
    """Map an offer row onto the HTTP contract."""
    return OfferResponse(
        offer_id=row.offer_id,
        seller_sku=row.seller_sku,
        product_id=row.product_id,
        product_name=row.product_name,
        store_slug=row.store_slug,
        vendor_name=row.vendor_name,
        country_code=_country_code(countries, row.country_id),
        status=row.status,
        publication_status=row.publication_status,
        currency_code=row.currency_code,
        list_price=row.list_price,
        updated_at=row.updated_at,
    )


def _country_id(countries: CountryReader, country_code: str | None) -> UUID | None:
    """Resolve an optional market code to its identifier."""
    if country_code is None:
        return None
    try:
        return countries.resolve_country(country_code).country_id
    except (KeyError, ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail="Country code is invalid.") from exc


def _country_code(countries: CountryReader, country_id: UUID) -> str | None:
    """Resolve an identifier to its market code, or `None` when unknown."""
    try:
        return countries.resolve_country_id(country_id).code
    except (KeyError, ValueError, UnicodeError):
        return None
