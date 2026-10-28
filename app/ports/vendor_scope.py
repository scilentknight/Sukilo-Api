"""Read whether a seller may publish in one country."""

from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.vendor.models import Store, VendorMember
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import MemberStatus, OnboardingStatus, VendorStatus


class VendorScopeReader(Protocol):
    """Report selling enablement and store ownership."""

    def is_selling_enabled(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return whether the vendor may sell in the country."""
        ...

    def owns_store(self, vendor_id: UUID, store_id: UUID, country_id: UUID) -> bool:
        """Return whether the store belongs to the vendor in that country."""
        ...

    def is_active_member(self, user_id: UUID, vendor_id: UUID) -> bool:
        """Return whether the account is an active member of the vendor."""
        ...


class SessionVendorScopeReader:
    """Read vendor scope from the current session."""

    def __init__(self, db: Session) -> None:
        """Initialize the reader.

        Args:
            db: Active SQLAlchemy session.
        """
        self.db = db
        self.vendors = VendorRepository(db)

    def is_selling_enabled(self, vendor_id: UUID, country_id: UUID) -> bool:
        """Return whether the vendor may sell in the country."""
        vendor = self.vendors.get_vendor(vendor_id)
        if vendor is None or vendor.status in {VendorStatus.SUSPENDED, VendorStatus.CLOSED}:
            return False
        country = self.vendors.get_country_for_market(vendor_id, country_id)
        return (
            country is not None
            and country.onboarding_status is OnboardingStatus.APPROVED
            and country.selling_enabled
        )

    def owns_store(self, vendor_id: UUID, store_id: UUID, country_id: UUID) -> bool:
        """Return whether the store belongs to the vendor in that country."""
        store = self.db.scalar(select(Store).where(Store.store_id == store_id))
        return (
            store is not None
            and store.vendor_id == vendor_id
            and store.country_id == country_id
        )

    def is_active_member(self, user_id: UUID, vendor_id: UUID) -> bool:
        """Return whether the account is an active member of the vendor."""
        member = self.db.scalar(
            select(VendorMember).where(
                VendorMember.vendor_id == vendor_id,
                VendorMember.user_id == user_id,
            )
        )
        return member is not None and member.status is MemberStatus.ACTIVE
