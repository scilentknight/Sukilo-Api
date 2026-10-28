"""Test draft organization rules without a database commit."""

from uuid import UUID, uuid4

from app.modules.vendor.service import VendorFoundationService
from app.modules.vendor.types import OnboardingStatus, VendorStatus, VendorType


class RecordingRepository:
    """Record staged vendor writes and reject unexpected side effects."""

    def __init__(self) -> None:
        """Initialize empty write records."""
        self.vendors: list[object] = []
        self.countries: list[object] = []
        self.commits = 0
        self.assignments = 0

    def add_vendor(self, vendor: object) -> None:
        """Record one staged organization."""
        self.vendors.append(vendor)

    def add_country(self, country: object) -> None:
        """Record one staged country application."""
        self.countries.append(country)

    def commit(self) -> None:
        """Record a commit if the service incorrectly owns the transaction."""
        self.commits += 1

    def grant_assignment(self) -> None:
        """Record a role grant if the service incorrectly assigns one."""
        self.assignments += 1


def test_draft_organization_is_pending_and_not_sellable() -> None:
    """Create a pending organization and a draft country with selling off."""
    repository = RecordingRepository()
    service = VendorFoundationService(repository)
    user_id = uuid4()
    home_country_id = UUID(int=4)
    market_country_id = UUID(int=8)
    country = service.create_draft_organization(
        created_by_user_id=user_id,
        legal_name="Acme Trading Pvt Ltd",
        display_name="Acme",
        vendor_type=VendorType.COMPANY,
        registration_country_id=home_country_id,
        market_country_id=market_country_id,
    )
    vendor = repository.vendors[0]
    assert vendor.status is VendorStatus.PENDING
    assert vendor.version == 1
    assert vendor.created_by_user_id == user_id
    assert vendor.registration_country_id == home_country_id
    assert country is repository.countries[0]
    assert country.onboarding_status is OnboardingStatus.DRAFT
    assert country.country_id == market_country_id
    assert country.vendor_id == vendor.vendor_id
    assert country.selling_enabled is False
    assert country.payout_enabled is False
    assert country.version == 1
    assert repository.commits == 0
    assert repository.assignments == 0
    assert len(repository.vendors) == 1
    assert len(repository.countries) == 1
