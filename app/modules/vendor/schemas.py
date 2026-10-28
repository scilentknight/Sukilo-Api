"""Describe seller application HTTP payloads."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.vendor.types import (
    AccountType,
    DocumentType,
    OnboardingStatus,
    StorefrontStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)


class ApplicationCreateRequest(BaseModel):
    """Describe a new draft seller application."""

    country_code: str = Field(min_length=2, max_length=2)
    legal_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=160)
    vendor_type: VendorType
    vendor_id: UUID | None = None


class ApplicationUpdateRequest(BaseModel):
    """Describe an optimistic application edit."""

    expected_version: int = Field(ge=1)
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    vendor_type: VendorType | None = None


class DocumentCreateRequest(BaseModel):
    """Describe one private document upload."""

    document_type: DocumentType
    document_number: str | None = Field(default=None, max_length=80)
    content_base64: str = Field(min_length=1)
    media_type: str = Field(min_length=1, max_length=120)


class SubmitApplicationRequest(BaseModel):
    """Describe seller terms accepted at submit time."""

    terms_version: str = Field(min_length=1, max_length=64)


class DocumentResponse(BaseModel):
    """Expose document metadata without a storage key."""

    vendor_document_id: UUID
    document_type: DocumentType
    verification_status: VerificationStatus


class ApplicationResponse(BaseModel):
    """Expose one seller country application."""

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
    version: int
    documents: list[DocumentResponse]


class ReviewReasonRequest(BaseModel):
    """Describe a rejection or information request."""

    reason: str = Field(min_length=1, max_length=500)


class InvitationCreateRequest(BaseModel):
    """Describe a staff invitation."""

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    email: str = Field(min_length=3, max_length=320)


class InvitationResponse(BaseModel):
    """Return a new invitation token once."""

    invitation_id: UUID
    expires_at: datetime
    token: str


class InvitationAcceptRequest(BaseModel):
    """Carry the one-time invitation token."""

    token: str = Field(min_length=20, max_length=512)


class MemberRevokeRequest(BaseModel):
    """Describe a staff revocation in one country."""

    country_code: str = Field(min_length=2, max_length=2)
    reason: str = Field(min_length=1, max_length=255)


class StoreUpsertRequest(BaseModel):
    """Describe one storefront and one translation."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    slug: str = Field(min_length=1, max_length=160)
    locale_code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    seo_title: str | None = Field(default=None, max_length=160)
    seo_description: str | None = Field(default=None, max_length=320)


class StoreCountryRequest(BaseModel):
    """Identify the store to activate."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)


class StoreResponse(BaseModel):
    """Expose store copy and selling flags."""

    store_id: UUID
    vendor_id: UUID
    country_id: UUID
    slug: str
    status: StorefrontStatus
    locale_code: str
    name: str
    selling_enabled: bool
    payout_enabled: bool


class PayoutAccountRequest(BaseModel):
    """Describe a new payout destination."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    account_type: AccountType
    currency_code: str = Field(min_length=3, max_length=3)
    account_holder_name: str = Field(min_length=1, max_length=200)
    account_number: str = Field(min_length=4, max_length=64)
    bank_name: str | None = Field(default=None, max_length=160)
    is_default: bool = True


class PayoutAccountResponse(BaseModel):
    """Expose a payout destination without the account secret."""

    vendor_payout_account_id: UUID
    currency_code: str
    account_number_masked: str
    is_default: bool
    verification_status: VerificationStatus


class WarehouseCreateRequest(BaseModel):
    """Describe a seller warehouse."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: UUID
    country_code: str = Field(min_length=2, max_length=2)
    code: str = Field(min_length=1, max_length=40)
    address_line_1: str = Field(min_length=1, max_length=200)
    city_text: str = Field(min_length=1, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    contact_name: str = Field(min_length=1, max_length=160)
    contact_phone: str = Field(min_length=1, max_length=32)
    fulfillment_enabled: bool = False
    pickup_enabled: bool = False
    status: StorefrontStatus = StorefrontStatus.DRAFT


class WarehouseResponse(BaseModel):
    """Expose one seller warehouse."""

    warehouse_id: UUID
    vendor_id: UUID
    country_id: UUID
    code: str
    status: StorefrontStatus
    fulfillment_enabled: bool

