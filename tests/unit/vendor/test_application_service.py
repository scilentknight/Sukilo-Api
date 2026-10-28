"""Test seller application rules without granting a badge."""

import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.auth_context import AuthContext
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.types import AccountStatus
from app.modules.vendor.applications import VendorApplicationService
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import Vendor, VendorCountry, VendorDocument
from app.modules.vendor.types import (
    DocumentType,
    OnboardingStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)
from app.ports.document_storage import MemoryDocumentStorage

NOW = datetime(2026, 10, 28, tzinfo=UTC)
APPLICANT = UUID(int=1)
NEPAL = DevelopmentCountryReader().resolve_country("NP").country_id


class Accounts:
    """Return one active applicant account."""

    def __init__(self, status: AccountStatus = AccountStatus.ACTIVE) -> None:
        """Initialize the account status."""
        self.status = status

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> object | None:
        """Return the applicant when the identifier matches."""
        del for_update
        if user_id != APPLICANT:
            return None
        return type("Account", (), {"user_id": user_id, "status": self.status, "email": None})()

    def get_by_email(self, email: str) -> None:
        """Return no account."""
        del email
        return None


class Repository:
    """Store vendor rows in memory."""

    def __init__(self) -> None:
        """Initialize empty collections."""
        self.vendors: list[Vendor] = []
        self.countries: list[VendorCountry] = []
        self.documents: list[VendorDocument] = []

    def add_vendor(self, vendor: Vendor) -> None:
        """Record a vendor."""
        self.vendors.append(vendor)

    def add_country(self, country: VendorCountry) -> None:
        """Record a country application."""
        self.countries.append(country)

    def get_vendor(self, vendor_id: UUID, *, for_update: bool = False) -> Vendor | None:
        """Return a stored vendor."""
        del for_update
        return next((row for row in self.vendors if row.vendor_id == vendor_id), None)

    def get_country(self, vendor_country_id: UUID, *, for_update: bool = False) -> VendorCountry | None:
        """Return a stored country application."""
        del for_update
        return next(
            (row for row in self.countries if row.vendor_country_id == vendor_country_id),
            None,
        )

    def get_country_for_market(self, vendor_id: UUID, country_id: UUID) -> VendorCountry | None:
        """Return an existing market application."""
        return next(
            (
                row
                for row in self.countries
                if row.vendor_id == vendor_id and row.country_id == country_id
            ),
            None,
        )

    def list_documents(self, vendor_country_id: UUID) -> list[VendorDocument]:
        """Return documents for one application."""
        return [
            row
            for row in self.documents
            if row.vendor_country_id == vendor_country_id
        ]

    def add_document(self, document: VendorDocument) -> None:
        """Record document metadata."""
        self.documents.append(document)

    def get_document(self, vendor_document_id: UUID) -> VendorDocument | None:
        """Return one document."""
        return next(
            (row for row in self.documents if row.vendor_document_id == vendor_document_id),
            None,
        )


class Authorizer:
    """Allow or deny review permission."""

    def __init__(self, allowed: bool = False) -> None:
        """Initialize the decision."""
        self.allowed = allowed

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Return the configured decision."""
        del actor, permission, country_id, vendor_id
        return self.allowed


class Grants:
    """Record badge grants that application flow must not call."""

    def __init__(self) -> None:
        """Initialize grant records."""
        self.calls: list[str] = []

    def grant_vendor_owner(self, *args: object) -> UUID:
        """Record an unexpected owner grant."""
        del args
        self.calls.append("owner")
        return uuid4()


def _service(repository: Repository, accounts: Accounts | None = None) -> VendorApplicationService:
    """Build an application service over memory fakes."""
    return VendorApplicationService(
        repository,
        accounts or Accounts(),
        DevelopmentCountryReader(),
        MemoryDocumentStorage(),
        Authorizer(),
        lambda: NOW,
    )


def _actor() -> AuthContext:
    """Return the applicant principal."""
    return AuthContext(APPLICANT, uuid4(), 1, ("password",), NOW, uuid4())


def test_submit_does_not_grant_vendor_owner() -> None:
    """Submit a complete draft without creating an owner badge."""
    repository = Repository()
    service = _service(repository)
    created = service.create_draft(
        _actor(),
        country_code="NP",
        legal_name="Acme Trading",
        display_name="Acme",
        vendor_type=VendorType.COMPANY,
        vendor_id=None,
    )
    service.add_document(
        _actor(),
        created.vendor_country_id,
        document_type=DocumentType.REGISTRATION,
        document_number="REG-SECRET",
        content_base64=base64.b64encode(b"certificate").decode(),
        media_type="application/pdf",
    )
    grants = Grants()
    submitted = service.submit(
        _actor(),
        created.vendor_country_id,
        terms_version="dev-1",
    )
    assert submitted.onboarding_status is OnboardingStatus.SUBMITTED
    assert submitted.selling_enabled is False
    assert submitted.vendor_status is VendorStatus.PENDING
    assert grants.calls == []
    assert "REG-SECRET" not in repr(repository.documents[0])
    assert repository.documents[0].storage_key not in repr(repository.documents[0])


def test_submit_rejects_a_missing_required_document() -> None:
    """Refuse submit until the country-required document type exists."""
    repository = Repository()
    service = _service(repository)
    created = service.create_draft(
        _actor(),
        country_code="NP",
        legal_name="Acme Trading",
        display_name="Acme",
        vendor_type=VendorType.COMPANY,
        vendor_id=None,
    )
    with pytest.raises(VendorServiceError) as captured:
        service.submit(_actor(), created.vendor_country_id, terms_version="dev-1")
    assert captured.value.status_code == 422


def test_guessed_document_returns_not_found() -> None:
    """Hide a document id that does not belong to the application."""
    repository = Repository()
    service = _service(repository)
    created = service.create_draft(
        _actor(),
        country_code="NP",
        legal_name="Acme Trading",
        display_name="Acme",
        vendor_type=VendorType.COMPANY,
        vendor_id=None,
    )
    with pytest.raises(VendorServiceError) as captured:
        service.get_document(_actor(), created.vendor_country_id, uuid4())
    assert captured.value.status_code == 404


def test_document_view_has_no_storage_key() -> None:
    """Keep the opaque storage key out of the document view."""
    repository = Repository()
    service = _service(repository)
    created = service.create_draft(
        _actor(),
        country_code="NP",
        legal_name="Acme Trading",
        display_name="Acme",
        vendor_type=VendorType.COMPANY,
        vendor_id=None,
    )
    view = service.add_document(
        _actor(),
        created.vendor_country_id,
        document_type=DocumentType.REGISTRATION,
        document_number="REG-SECRET",
        content_base64=base64.b64encode(b"certificate").decode(),
        media_type="application/pdf",
    )
    assert view.verification_status is VerificationStatus.PENDING
    assert not hasattr(view, "storage_key")
    assert not hasattr(view, "document_number")
    assert NEPAL == created.country_id
