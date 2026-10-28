"""Test country review decisions and audit redaction."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.access.vendor_grants import VendorGrantError
from app.modules.identity.types import AccountStatus
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import Vendor, VendorCountry, VendorMember
from app.modules.vendor.reviews import VendorReviewService
from app.modules.vendor.types import (
    OnboardingStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)
from app.ports.audit import AuditEventInput, NoOpAuditWriter

NOW = datetime(2026, 10, 28, tzinfo=UTC)
APPLICANT = UUID(int=1)
REVIEWER = UUID(int=2)
NEPAL = UUID(int=10)
UAE = UUID(int=20)


class Accounts:
    """Return the applicant account."""

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
    """Store one submitted application."""

    def __init__(self, country_id: UUID = NEPAL) -> None:
        """Initialize the submitted application."""
        self.vendor = Vendor(
            vendor_id=uuid4(),
            legal_name="Acme Trading",
            display_name="Acme",
            vendor_type=VendorType.COMPANY,
            registration_country_id=country_id,
            status=VendorStatus.PENDING,
            created_by_user_id=APPLICANT,
            version=1,
        )
        self.country = VendorCountry(
            vendor_country_id=uuid4(),
            vendor_id=self.vendor.vendor_id,
            country_id=country_id,
            onboarding_status=OnboardingStatus.SUBMITTED,
            verification_status=VerificationStatus.PENDING,
            selling_enabled=False,
            payout_enabled=False,
            version=1,
        )
        self.members: list[VendorMember] = []

    def get_vendor(self, vendor_id: UUID, *, for_update: bool = False) -> Vendor | None:
        """Return the stored vendor."""
        del for_update
        if vendor_id != self.vendor.vendor_id:
            return None
        return self.vendor

    def get_country(self, vendor_country_id: UUID, *, for_update: bool = False) -> VendorCountry | None:
        """Return the stored application."""
        del for_update
        if vendor_country_id != self.country.vendor_country_id:
            return None
        return self.country

    def add_member(self, member: VendorMember) -> None:
        """Record a membership."""
        self.members.append(member)

    def list_countries_for_review(self, country_id: UUID) -> list[VendorCountry]:
        """Return the application when it is in the requested market."""
        if self.country.country_id != country_id:
            return []
        return [self.country]


class Authorizer:
    """Allow review only for one configured country."""

    def __init__(self, country_id: UUID | None) -> None:
        """Initialize the allowed country."""
        self.country_id = country_id

    def allows(self, actor: AuthContext, permission: str, country_id: UUID, vendor_id: UUID | None) -> bool:
        """Allow the configured country only."""
        del actor, permission, vendor_id
        return self.country_id == country_id


class Grants:
    """Record or fail badge grants."""

    def __init__(self, fail: bool = False) -> None:
        """Initialize grant behavior."""
        self.fail = fail
        self.owners: list[tuple[UUID, UUID, UUID]] = []

    def grant_vendor_owner(
        self,
        user_id: UUID,
        vendor_id: UUID,
        country_id: UUID,
        actor_user_id: UUID,
    ) -> UUID:
        """Record an owner grant or fail before it is stored."""
        del actor_user_id
        if self.fail:
            raise VendorGrantError("grant failed")
        self.owners.append((user_id, vendor_id, country_id))
        return uuid4()

    def grant_vendor_staff(self, *args: object) -> UUID:
        """Record an unexpected staff grant."""
        del args
        raise AssertionError("Staff grant is not part of review.")


class Audit:
    """Record staged audit events."""

    def __init__(self) -> None:
        """Initialize an empty event list."""
        self.events: list[AuditEventInput] = []

    def stage(self, event: AuditEventInput, transaction: DatabaseTransaction) -> None:
        """Record one event."""
        del transaction
        self.events.append(event)


def _service(
    repository: Repository,
    authorizer: Authorizer,
    grants: Grants,
    audit: Audit | NoOpAuditWriter,
) -> VendorReviewService:
    """Build a review service over fakes."""
    return VendorReviewService(
        repository,
        Accounts(),
        object(),
        authorizer,
        grants,
        audit,
        DatabaseTransaction(object()),
        lambda: NOW,
        lambda token, actor, action_class: None,
    )


def _reviewer() -> AuthContext:
    """Return a country reviewer principal."""
    return AuthContext(REVIEWER, uuid4(), 1, ("password", "otp"), NOW, uuid4())


def test_approval_grants_owner_and_leaves_selling_disabled() -> None:
    """Approve into an active owner membership without enabling selling."""
    repository = Repository()
    grants = Grants()
    audit = Audit()
    service = _service(repository, Authorizer(NEPAL), grants, audit)
    view = service.approve(_reviewer(), repository.country.vendor_country_id, step_up_token="grant")
    assert view.onboarding_status is OnboardingStatus.APPROVED
    assert view.selling_enabled is False
    assert view.payout_enabled is False
    assert repository.vendor.status is VendorStatus.ACTIVE
    assert repository.members[0].status.value == "ACTIVE"
    assert repository.members[0].accepted_at == NOW
    assert grants.owners == [(APPLICANT, repository.vendor.vendor_id, NEPAL)]
    assert audit.events[0].after_data == {"decision": "APPROVED"}
    assert "document_number" not in audit.events[0].after_data


def test_approval_failure_does_not_hide_the_member_write() -> None:
    """Leave the member staged when the badge grant fails so the caller can roll back."""
    repository = Repository()
    grants = Grants(fail=True)
    service = _service(repository, Authorizer(NEPAL), grants, Audit())
    with pytest.raises(VendorGrantError):
        service.approve(_reviewer(), repository.country.vendor_country_id, step_up_token="grant")
    assert repository.members
    assert grants.owners == []


def test_nepal_reviewer_cannot_approve_uae() -> None:
    """Deny a reviewer whose country is not the application country."""
    repository = Repository(UAE)
    service = _service(repository, Authorizer(NEPAL), Grants(), Audit())
    with pytest.raises(VendorServiceError) as captured:
        service.approve(_reviewer(), repository.country.vendor_country_id, step_up_token="grant")
    assert captured.value.status_code == 403
    assert repository.country.onboarding_status is OnboardingStatus.SUBMITTED
    assert repository.members == []


def test_applicant_cannot_approve_own_application() -> None:
    """Deny approval when the actor created the application."""
    repository = Repository()
    service = _service(repository, Authorizer(NEPAL), Grants(), Audit())
    applicant = AuthContext(APPLICANT, uuid4(), 1, ("password", "otp"), NOW, uuid4())
    with pytest.raises(VendorServiceError) as captured:
        service.approve(applicant, repository.country.vendor_country_id, step_up_token="grant")
    assert captured.value.status_code == 403
    assert repository.members == []


def test_reject_grants_nothing() -> None:
    """Reject with a reason and leave membership empty."""
    repository = Repository()
    grants = Grants()
    audit = Audit()
    service = _service(repository, Authorizer(NEPAL), grants, audit)
    view = service.reject(
        _reviewer(),
        repository.country.vendor_country_id,
        reason="Missing tax certificate",
        step_up_token="grant",
    )
    assert view.onboarding_status is OnboardingStatus.REJECTED
    assert grants.owners == []
    assert repository.members == []
    assert "REG-SECRET" not in str(audit.events[0].after_data)


def test_noop_audit_cannot_approve() -> None:
    """Refuse approval when the audit writer would discard the decision."""
    repository = Repository()
    service = _service(repository, Authorizer(NEPAL), Grants(), NoOpAuditWriter())
    with pytest.raises(VendorServiceError) as captured:
        service.approve(_reviewer(), repository.country.vendor_country_id, step_up_token="grant")
    assert captured.value.status_code == 503
    assert repository.members == []
