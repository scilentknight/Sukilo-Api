"""Create pending seller organizations without committing."""

from uuid import UUID, uuid4

from app.modules.vendor.models import Vendor, VendorCountry
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import (
    OnboardingStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)


class VendorFoundationService:
    """Create a pending organization and one draft country row."""

    def __init__(self, repository: VendorRepository) -> None:
        """Initialize the foundation service.

        Args:
            repository: Vendor persistence implementation.
        """
        self.repository = repository

    def create_draft_organization(
        self,
        *,
        created_by_user_id: UUID,
        legal_name: str,
        display_name: str,
        vendor_type: VendorType,
        registration_country_id: UUID,
        market_country_id: UUID,
    ) -> VendorCountry:
        """Insert one pending vendor and one draft country application.

        Args:
            created_by_user_id: Existing account that owns the draft.
            legal_name: Registered organization name.
            display_name: Name shown to shoppers later.
            vendor_type: Legal form of the organization.
            registration_country_id: Home country of the organization.
            market_country_id: Country where the draft application applies.

        Returns:
            The staged draft country row.
        """
        vendor = Vendor(
            vendor_id=uuid4(),
            legal_name=legal_name,
            display_name=display_name,
            vendor_type=vendor_type,
            registration_country_id=registration_country_id,
            status=VendorStatus.PENDING,
            created_by_user_id=created_by_user_id,
            version=1,
        )
        country = VendorCountry(
            vendor_country_id=uuid4(),
            vendor_id=vendor.vendor_id,
            country_id=market_country_id,
            onboarding_status=OnboardingStatus.DRAFT,
            verification_status=VerificationStatus.PENDING,
            selling_enabled=False,
            payout_enabled=False,
            version=1,
        )
        self.repository.add_vendor(vendor)
        self.repository.add_country(country)
        return country
