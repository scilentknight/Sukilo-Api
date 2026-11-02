"""Describe the read-only administration HTTP contracts."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.modules.catalog.types import (
    ModerationStatus,
    OfferStatus,
    ProductStatus,
    PublicationStatus,
    RecordStatus,
)
from app.modules.identity.types import AccountStatus
from app.modules.vendor.types import (
    AccountType,
    OnboardingStatus,
    StorefrontStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)


class MetricResponse(BaseModel):
    """Expose one total/active/pending counter triplet."""

    total: int
    active: int
    pending: int


class PendingApplicationSummary(BaseModel):
    """Expose one application waiting for a review decision."""

    vendor_id: UUID
    vendor_country_id: UUID
    legal_name: str
    display_name: str
    country_id: UUID
    country_code: str | None
    onboarding_status: OnboardingStatus
    created_at: datetime


class AdminOverviewResponse(BaseModel):
    """Expose headline counts for the admin dashboard."""

    customers: MetricResponse
    vendors: MetricResponse
    applications: MetricResponse
    catalog_products: MetricResponse
    categories: int
    brands: int
    offers: int
    published_offers: int
    pending_applications: list[PendingApplicationSummary]


class AdminCountryResponse(BaseModel):
    """Expose one country that has marketplace data."""

    country_id: UUID
    code: str
    applications: int
    pending_applications: int
    enabled_categories: int
    published_offers: int


class CustomerResponse(BaseModel):
    """Expose one customer account without credentials."""

    user_id: UUID
    email_masked: str | None
    phone_masked: str | None
    status: AccountStatus
    email_verified: bool
    phone_verified: bool
    is_vendor: bool
    vendor_name: str | None
    created_at: datetime
    last_login_at: datetime | None


class CustomerListResponse(BaseModel):
    """Expose one customer page."""

    items: list[CustomerResponse]
    total: int
    limit: int
    offset: int


class VendorCountryResponse(BaseModel):
    """Expose one seller country application inside a vendor row."""

    vendor_country_id: UUID
    country_id: UUID
    country_code: str | None
    onboarding_status: OnboardingStatus
    verification_status: VerificationStatus
    selling_enabled: bool
    payout_enabled: bool


class VendorResponse(BaseModel):
    """Expose one seller organization."""

    vendor_id: UUID
    legal_name: str
    display_name: str
    vendor_type: VendorType
    status: VendorStatus
    owner_email_masked: str | None
    created_at: datetime
    countries: list[VendorCountryResponse]


class VendorListResponse(BaseModel):
    """Expose one vendor page."""

    items: list[VendorResponse]
    total: int
    limit: int
    offset: int


class StoreResponse(BaseModel):
    """Expose one seller storefront."""

    store_id: UUID
    name: str | None
    slug: str
    status: StorefrontStatus
    country_id: UUID
    country_code: str | None
    rating: Decimal
    followers_count: int


class WarehouseResponse(BaseModel):
    """Expose one seller warehouse."""

    warehouse_id: UUID
    code: str
    country_id: UUID
    country_code: str | None
    city_text: str
    status: StorefrontStatus
    fulfillment_enabled: bool
    pickup_enabled: bool


class PayoutAccountResponse(BaseModel):
    """Expose one masked seller payout destination."""

    vendor_payout_account_id: UUID
    account_type: AccountType
    currency_code: str
    account_number_masked: str
    is_default: bool
    verification_status: VerificationStatus


class VendorDetailResponse(VendorResponse):
    """Expose one seller organization with its operational records."""

    stores: list[StoreResponse]
    warehouses: list[WarehouseResponse]
    payout_accounts: list[PayoutAccountResponse]


class DocumentSummaryResponse(BaseModel):
    """Expose one application document without its storage key."""

    vendor_document_id: UUID
    document_type: str
    verification_status: VerificationStatus


class ApplicationResponse(BaseModel):
    """Expose one seller application for the review workspace."""

    vendor_id: UUID
    vendor_country_id: UUID
    country_id: UUID
    country_code: str | None
    legal_name: str
    display_name: str
    vendor_type: VendorType
    vendor_status: VendorStatus
    onboarding_status: OnboardingStatus
    selling_enabled: bool
    payout_enabled: bool
    owner_email_masked: str | None
    created_at: datetime
    documents: list[DocumentSummaryResponse]


class ApplicationListResponse(BaseModel):
    """Expose one application page."""

    items: list[ApplicationResponse]
    total: int
    limit: int
    offset: int


class CategoryResponse(BaseModel):
    """Expose one canonical category."""

    category_id: UUID
    parent_id: UUID | None
    code: str
    name: str | None
    status: RecordStatus
    enabled_countries: list[str]


class BrandResponse(BaseModel):
    """Expose one canonical brand."""

    brand_id: UUID
    name: str
    status: RecordStatus
    enabled_countries: list[str]


class ProductCountryResponse(BaseModel):
    """Expose one country moderation state for a catalog product."""

    country_id: UUID
    country_code: str | None
    moderation_status: ModerationStatus


class CatalogProductResponse(BaseModel):
    """Expose one shared catalog product."""

    product_id: UUID
    name: str | None
    category_code: str
    category_name: str | None
    brand_name: str
    status: ProductStatus
    created_by_email_masked: str | None
    created_at: datetime
    sku_count: int
    offer_count: int
    countries: list[ProductCountryResponse]


class CatalogProductListResponse(BaseModel):
    """Expose one catalog product page."""

    items: list[CatalogProductResponse]
    total: int
    limit: int
    offset: int


class OfferResponse(BaseModel):
    """Expose one seller offer with its country price."""

    offer_id: UUID
    seller_sku: str
    product_id: UUID
    product_name: str | None
    store_slug: str
    vendor_name: str
    country_code: str | None
    status: OfferStatus
    publication_status: PublicationStatus
    currency_code: str
    list_price: Decimal
    updated_at: datetime


class OfferListResponse(BaseModel):
    """Expose one offer page."""

    items: list[OfferResponse]
    total: int
    limit: int
    offset: int
