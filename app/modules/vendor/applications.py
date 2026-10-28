"""Save and submit one seller country application."""

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.auth_context import AuthContext
from app.modules.identity.types import AccountStatus
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import Vendor, VendorCountry, VendorDocument
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.service import VendorFoundationService
from app.modules.vendor.types import (
    DocumentType,
    OnboardingStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)
from app.modules.access.vendor_grants import VendorAuthorizer
from app.ports.country import CountryReader, CountryReference
from app.ports.document_storage import DocumentStorage

_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_EDITABLE = frozenset({OnboardingStatus.DRAFT, OnboardingStatus.REVIEW})
_REQUIRED_DOCUMENTS = {
    VendorType.COMPANY: frozenset({DocumentType.REGISTRATION}),
    VendorType.INDIVIDUAL: frozenset({DocumentType.OWNER_ID}),
    VendorType.SOLE_TRADER: frozenset({DocumentType.REGISTRATION}),
}


@dataclass(frozen=True, slots=True)
class DocumentView:
    """Expose document metadata without the storage key or number."""

    vendor_document_id: UUID
    document_type: DocumentType
    verification_status: VerificationStatus


@dataclass(frozen=True, slots=True)
class ApplicationView:
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
    documents: tuple[DocumentView, ...]


class AccountReader(Protocol):
    """Describe account lookups required by seller applications."""

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> object:
        """Return one account."""
        ...

    def get_by_email(self, email: str) -> object:
        """Return one account by normalized email."""
        ...


class VendorApplicationService:
    """Create, update, and submit seller country applications."""

    def __init__(
        self,
        repository: VendorRepository,
        accounts: AccountReader,
        countries: CountryReader,
        storage: DocumentStorage,
        authorizer: VendorAuthorizer,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize application dependencies."""
        self.repository = repository
        self.accounts = accounts
        self.countries = countries
        self.storage = storage
        self.authorizer = authorizer
        self.clock = clock
        self.foundation = VendorFoundationService(repository)

    def create_draft(
        self,
        actor: AuthContext,
        *,
        country_code: str,
        legal_name: str,
        display_name: str,
        vendor_type: VendorType,
        vendor_id: UUID | None,
    ) -> ApplicationView:
        """Create a draft organization or another draft country."""
        self._require_active(actor.user_id)
        country = self._market(country_code)
        if vendor_id is None:
            row = self.foundation.create_draft_organization(
                created_by_user_id=actor.user_id,
                legal_name=legal_name,
                display_name=display_name,
                vendor_type=vendor_type,
                registration_country_id=country.country_id,
                market_country_id=country.country_id,
            )
            return self._view(self._owned_vendor(actor, row.vendor_id), row)
        vendor = self._owned_vendor(actor, vendor_id)
        if self.repository.get_country_for_market(vendor.vendor_id, country.country_id):
            raise VendorServiceError(409, "A country application already exists.")
        row = VendorCountry(
            vendor_id=vendor.vendor_id,
            country_id=country.country_id,
            onboarding_status=OnboardingStatus.DRAFT,
            verification_status=VerificationStatus.PENDING,
            selling_enabled=False,
            payout_enabled=False,
            version=1,
        )
        self.repository.add_country(row)
        return self._view(vendor, row)

    def update_draft(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        *,
        expected_version: int,
        legal_name: str | None,
        display_name: str | None,
        vendor_type: VendorType | None,
    ) -> ApplicationView:
        """Update a draft or review application when the version matches."""
        country = self._editable_country(actor, vendor_country_id, expected_version)
        vendor = self._owned_vendor(actor, country.vendor_id)
        if legal_name is not None:
            vendor.legal_name = legal_name
        if display_name is not None:
            vendor.display_name = display_name
        if vendor_type is not None:
            vendor.vendor_type = vendor_type
        country.version += 1
        return self._view(vendor, country)

    def add_document(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        *,
        document_type: DocumentType,
        document_number: str | None,
        content_base64: str,
        media_type: str,
    ) -> DocumentView:
        """Store private document bytes and keep only the opaque key."""
        country = self._editable_country(actor, vendor_country_id, None)
        content = _decode_content(content_base64)
        if not media_type.strip():
            raise VendorServiceError(422, "Document media type is required.")
        key = self.storage.put_private(content, media_type)
        document = VendorDocument(
            vendor_country_id=country.vendor_country_id,
            document_type=document_type,
            document_number=document_number,
            storage_key=key,
            verification_status=VerificationStatus.PENDING,
        )
        self.repository.add_document(document)
        return _document_view(document)

    def submit(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        *,
        terms_version: str,
    ) -> ApplicationView:
        """Move a complete draft or review application to submitted."""
        self._require_active(actor.user_id)
        country = self._lock_country(vendor_country_id)
        vendor = self._owned_vendor(actor, country.vendor_id)
        if country.onboarding_status not in _EDITABLE:
            raise VendorServiceError(409, "Application cannot be submitted.")
        self._require_onboarding(country.country_id)
        active_terms = self.countries.get_active_policy_version(
            country.country_id,
            "SELLER_TERMS",
        )
        if terms_version != active_terms:
            raise VendorServiceError(422, "Seller terms version is not active.")
        present = {
            document.document_type
            for document in self.repository.list_documents(country.vendor_country_id)
            if document.verification_status is not VerificationStatus.REJECTED
        }
        missing = _REQUIRED_DOCUMENTS[vendor.vendor_type] - present
        if missing:
            raise VendorServiceError(422, "A required document type is missing.")
        country.terms_version = terms_version
        country.terms_accepted_at = self.clock()
        country.onboarding_status = OnboardingStatus.SUBMITTED
        country.selling_enabled = False
        country.payout_enabled = False
        country.version += 1
        return self._view(vendor, country)

    def get_application(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
    ) -> ApplicationView:
        """Return an application to its applicant or a same-country reviewer."""
        country = self.repository.get_country(vendor_country_id)
        if country is None:
            raise VendorServiceError(404, "Application is unavailable.")
        vendor = self.repository.get_vendor(country.vendor_id)
        if vendor is None:
            raise VendorServiceError(404, "Application is unavailable.")
        if vendor.created_by_user_id == actor.user_id:
            return self._view(vendor, country)
        if self.authorizer.allows(
            actor,
            "vendor.application.review",
            country.country_id,
            country.vendor_id,
        ):
            return self._view(vendor, country)
        raise VendorServiceError(404, "Application is unavailable.")

    def get_document(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        vendor_document_id: UUID,
    ) -> DocumentView:
        """Return document metadata when the caller can see the application."""
        self.get_application(actor, vendor_country_id)
        document = self.repository.get_document(vendor_document_id)
        if document is None or document.vendor_country_id != vendor_country_id:
            raise VendorServiceError(404, "Document is unavailable.")
        return _document_view(document)

    def _editable_country(
        self,
        actor: AuthContext,
        vendor_country_id: UUID,
        expected_version: int | None,
    ) -> VendorCountry:
        """Lock a draft or review row owned by the actor."""
        country = self._lock_country(vendor_country_id)
        self._owned_vendor(actor, country.vendor_id)
        if country.onboarding_status not in _EDITABLE:
            raise VendorServiceError(409, "Application cannot be edited.")
        if expected_version is not None and country.version != expected_version:
            raise VendorServiceError(409, "Application version is stale.")
        return country

    def _lock_country(self, vendor_country_id: UUID) -> VendorCountry:
        """Lock one country application or hide a missing row."""
        country = self.repository.get_country(vendor_country_id, for_update=True)
        if country is None:
            raise VendorServiceError(404, "Application is unavailable.")
        return country

    def _owned_vendor(self, actor: AuthContext, vendor_id: UUID) -> Vendor:
        """Return the actor's organization or hide another seller's row."""
        vendor = self.repository.get_vendor(vendor_id, for_update=True)
        if vendor is None or vendor.created_by_user_id != actor.user_id:
            raise VendorServiceError(404, "Application is unavailable.")
        return vendor

    def _require_active(self, user_id: UUID) -> None:
        """Require an active account before a seller mutation."""
        account = self.accounts.get_by_id(user_id, for_update=True)
        status = getattr(account, "status", None)
        if account is None or status != AccountStatus.ACTIVE:
            raise VendorServiceError(403, "Account cannot apply as a seller.")

    def _market(self, country_code: str) -> CountryReference:
        """Resolve a market and require seller onboarding."""
        try:
            country = self.countries.resolve_country(country_code)
        except (KeyError, ValueError, UnicodeError) as exc:
            raise VendorServiceError(422, "Country is unavailable.") from exc
        self._require_onboarding(country.country_id)
        return country

    def _require_onboarding(self, country_id: UUID) -> None:
        """Reject a market that does not accept seller applications."""
        if not self.countries.is_operation_enabled(country_id, "SELLER_ONBOARDING"):
            raise VendorServiceError(403, "Seller onboarding is not enabled.")

    def _view(self, vendor: Vendor, country: VendorCountry) -> ApplicationView:
        """Build a response that omits storage keys and document numbers."""
        documents = tuple(
            _document_view(document)
            for document in self.repository.list_documents(country.vendor_country_id)
        )
        return ApplicationView(
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
            version=country.version,
            documents=documents,
        )


def _document_view(document: VendorDocument) -> DocumentView:
    """Copy public document fields and drop the storage key."""
    return DocumentView(
        document.vendor_document_id,
        document.document_type,
        document.verification_status,
    )


def _decode_content(content_base64: str) -> bytes:
    """Decode a bounded document payload."""
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VendorServiceError(422, "Document content is invalid.") from exc
    if not content or len(content) > _MAX_DOCUMENT_BYTES:
        raise VendorServiceError(422, "Document content is invalid.")
    return content
