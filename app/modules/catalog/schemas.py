"""Describe catalog taxonomy HTTP payloads."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.catalog.types import (
    OfferStatus,
    ProductStatus,
    PublicationStatus,
    RequestStatus,
    ReviewDecision,
)


class CategoryCreateRequest(BaseModel):
    """Describe a canonical category."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    parent_id: UUID | None = None
    locale_code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=160)


class CategoryResponse(BaseModel):
    """Expose one category and the requested locale label."""

    category_id: UUID
    parent_id: UUID | None
    code: str
    name: str | None


class CountryEnableRequest(BaseModel):
    """Identify the country that should enable a record."""

    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(min_length=2, max_length=2)


class CategoryRequestCreate(BaseModel):
    """Describe a vendor category proposal."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    proposed_code: str = Field(min_length=1, max_length=80)
    proposed_parent_id: UUID | None = None
    category_id: UUID | None = None


class CategoryDecisionRequest(BaseModel):
    """Describe a category review decision."""

    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision


class CategoryRequestResponse(BaseModel):
    """Expose a category proposal."""

    request_id: UUID
    status: RequestStatus
    category_id: UUID | None


class BrandRequestCreate(BaseModel):
    """Describe a vendor brand proposal."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    proposed_name: str = Field(min_length=1, max_length=160)


class BrandDecisionRequest(BaseModel):
    """Describe a brand review decision."""

    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    link_brand_id: UUID | None = None


class BrandRequestResponse(BaseModel):
    """Expose a brand proposal."""

    request_id: UUID
    status: RequestStatus
    brand_id: UUID | None


class BrandResponse(BaseModel):
    """Expose one enabled brand."""

    brand_id: UUID
    name: str


class AttributeLinkRequest(BaseModel):
    """Describe one attribute attached to a category."""

    model_config = ConfigDict(extra="forbid")

    attribute_id: UUID
    required: bool = False


class CategoryAttributesRequest(BaseModel):
    """Replace the attribute set for one category."""

    model_config = ConfigDict(extra="forbid")

    attributes: list[AttributeLinkRequest]


class AttributeValueRequest(BaseModel):
    """Describe one attribute value with exactly one representation."""

    model_config = ConfigDict(extra="forbid")

    attribute_id: UUID
    option_id: UUID | None = None
    value_text: str | None = None
    value_number: Decimal | None = None


class ProductCreateRequest(BaseModel):
    """Describe a draft shared product."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    category_id: UUID
    brand_id: UUID
    locale_code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    attributes: list[AttributeValueRequest] = Field(default_factory=list)


class ProductResponse(BaseModel):
    """Expose a shared product."""

    product_id: UUID
    status: ProductStatus


class SkuCreateRequest(BaseModel):
    """Describe one product variant."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    sku_code: str = Field(min_length=1, max_length=80)
    attributes: list[AttributeValueRequest] = Field(default_factory=list)


class SkuResponse(BaseModel):
    """Expose one SKU."""

    sku_id: UUID
    product_id: UUID
    sku_code: str


class MediaCreateRequest(BaseModel):
    """Describe private media bytes for one product."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    content_base64: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=120)
    sort_order: int = 0
    is_primary: bool = False


class MediaResponse(BaseModel):
    """Expose media metadata to the uploading vendor."""

    media_id: UUID
    storage_key: str
    media_type: str
    sort_order: int
    is_primary: bool


class ProductSubmitRequest(BaseModel):
    """Name the countries that should review a product."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_codes: list[str] = Field(min_length=1)


class ProductModerateRequest(BaseModel):
    """Describe one country moderation decision."""

    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(min_length=2, max_length=2)
    decision: ReviewDecision


class OfferCreateRequest(BaseModel):
    """Describe a draft seller offer."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    store_id: UUID
    sku_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    seller_sku: str = Field(min_length=1, max_length=80)
    list_price: Decimal
    currency_code: str = Field(min_length=3, max_length=3)


class OfferPublishRequest(BaseModel):
    """Name the country that should publish an offer."""

    model_config = ConfigDict(extra="forbid")

    country_code: str = Field(min_length=2, max_length=2)


class OfferResponse(BaseModel):
    """Expose one seller offer and its country price."""

    offer_id: UUID
    vendor_id: UUID
    store_id: UUID
    sku_id: UUID
    status: OfferStatus
    publication_status: PublicationStatus
    currency_code: str
    list_price: Decimal


class PublicOfferResponse(BaseModel):
    """Expose a published offer without media keys or stock."""

    model_config = ConfigDict(extra="forbid")

    offer_id: UUID
    store_id: UUID
    sku_id: UUID
    product_id: UUID
    currency_code: str
    list_price: Decimal
    name: str
