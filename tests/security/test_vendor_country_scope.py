"""Test seller country isolation for review and documents."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.identity.types import AccountStatus
from app.modules.vendor.applications import VendorApplicationService
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import Vendor, VendorCountry
from app.modules.vendor.reviews import VendorReviewService
from app.modules.vendor.types import OnboardingStatus, VendorStatus, VendorType, VerificationStatus
from app.ports.document_storage import MemoryDocumentStorage

NOW = datetime(2026, 10, 28, tzinfo=UTC)
APPLICANT = UUID(int=1)
REVIEWER = UUID(int=2)
NEPAL = UUID(int=10)
UAE = UUID(int=20)


class Accounts:
    """Return the applicant."""

    def get_by_id(self, user_id: UUID, *, for_update: bool = False) -> object | None:
        """Return an active applicant."""
        del for_update
        if user_id != APPLICANT:
            return None
        return type("Account", (), {"user_id": user_id, "status": AccountStatus.ACTIVE})()

    def get_by_email(self, email: str) -> None:
        """Return no email match."""
        del email
        return None


class Repository:
    """Store one UAE application."""

    def __init__(self) -> None:
        """Initialize the foreign application."""
        self.vendor = Vendor(
            vendor_id=uuid4(),
            legal_name="Gulf Trading",
            display_name="Gulf",
            vendor_type=VendorType.COMPANY,
            registration_country_id=UAE,
            status=VendorStatus.PENDING,
            created_by_user_id=APPLICANT,
            version=1,
        )
        self.country = VendorCountry(
            vendor_country_id=uuid4(),
            vendor_id=self.vendor.vendor_id,
            country_id=UAE,
            onboarding_status=OnboardingStatus.SUBMITTED,
            verification_status=VerificationStatus.PENDING,
            selling_enabled=False,
            payout_enabled=False,
            version=1,
        )
        self.members: list[object] = []

    def get_vendor(self, vendor_id: UUID, *, for_update: bool = False) -> Vendor | None:
        """Return the vendor."""
        del for_update
        if vendor_id != self.vendor.vendor_id:
            return None
        return self.vendor

    def get_country(self, vendor_country_id: UUID, *, for_update: bool = False) -> VendorCountry | None:
        """Return the application."""
        del for_update
        if vendor_country_id != self.country.vendor_country_id:
            return None
        return self.country

    def add_member(self, member: object) -> None:
        """Record a membership."""
        self.members.append(member)

    def list_documents(self, vendor_country_id: UUID) -> list[object]:
        """Return no documents."""
        del vendor_country_id
        return []

    def get_document(self, vendor_document_id: UUID) -> None:
        """Hide every guessed document id."""
        del vendor_document_id
        return None


class Authorizer:
    """Allow only Nepal."""

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Match Nepal only."""
        del actor, permission, vendor_id
        return country_id == NEPAL


def test_foreign_reviewer_cannot_approve_another_country() -> None:
    """Deny a Nepal reviewer who targets a UAE application."""
    repository = Repository()
    service = VendorReviewService(
        repository,
        Accounts(),
        object(),
        Authorizer(),
        type("Grants", (), {"grant_vendor_owner": lambda *args: uuid4()})(),
        type("Audit", (), {"stage": lambda *args, **kwargs: None})(),
        DatabaseTransaction(object()),
        lambda: NOW,
        lambda token, actor, action_class: None,
    )
    reviewer = AuthContext(REVIEWER, uuid4(), 1, ("password", "otp"), NOW, uuid4())
    with pytest.raises(VendorServiceError) as captured:
        service.approve(reviewer, repository.country.vendor_country_id, step_up_token="grant")
    assert captured.value.status_code == 403
    assert repository.members == []


def test_guessed_document_id_is_not_found_for_the_applicant() -> None:
    """Return not found for a document id the application does not own."""
    repository = Repository()
    service = VendorApplicationService(
        repository,
        Accounts(),
        object(),
        MemoryDocumentStorage(),
        Authorizer(),
        lambda: NOW,
    )
    applicant = AuthContext(APPLICANT, uuid4(), 1, ("password",), NOW, uuid4())
    with pytest.raises(VendorServiceError) as captured:
        service.get_document(applicant, repository.country.vendor_country_id, uuid4())
    assert captured.value.status_code == 404
