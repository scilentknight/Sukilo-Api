"""Describe catalog taxonomy HTTP payloads."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.catalog.types import RequestStatus, ReviewDecision


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
