"""Persist seller organizations without committing the transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.vendor.models import Vendor, VendorCountry


class VendorRepository:
    """Provide focused persistence operations for seller organizations."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository.

        Args:
            db: Active SQLAlchemy session.
        """
        self.db = db

    def get_vendor(
        self,
        vendor_id: UUID,
        *,
        for_update: bool = False,
    ) -> Vendor | None:
        """Return one organization by its identifier.

        Args:
            vendor_id: Stable organization identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching organization, or `None`.
        """
        statement = select(Vendor).where(Vendor.vendor_id == vendor_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_country(
        self,
        vendor_country_id: UUID,
        *,
        for_update: bool = False,
    ) -> VendorCountry | None:
        """Return one country application by its identifier.

        Args:
            vendor_country_id: Stable country-application identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching country row, or `None`.
        """
        statement = select(VendorCountry).where(
            VendorCountry.vendor_country_id == vendor_country_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_countries_for_vendor(self, vendor_id: UUID) -> list[VendorCountry]:
        """Return country applications for one organization.

        Args:
            vendor_id: Organization whose country rows are requested.

        Returns:
            Country rows ordered by creation time and identifier.
        """
        statement = (
            select(VendorCountry)
            .where(VendorCountry.vendor_id == vendor_id)
            .order_by(VendorCountry.created_at, VendorCountry.vendor_country_id)
        )
        return list(self.db.scalars(statement))

    def add_vendor(self, vendor: Vendor) -> None:
        """Stage one organization row.

        Args:
            vendor: Organization to insert.
        """
        self.db.add(vendor)

    def add_country(self, country: VendorCountry) -> None:
        """Stage one country application row.

        Args:
            country: Country application to insert.
        """
        self.db.add(country)
