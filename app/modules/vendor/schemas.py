"""Describe seller application HTTP payloads."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.vendor.types import (
    DocumentType,
    OnboardingStatus,
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
