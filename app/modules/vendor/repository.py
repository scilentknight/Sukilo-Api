"""Persist seller organizations without committing the transaction."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.vendor.models import (
    Store,
    StoreTranslation,
    Vendor,
    VendorCountry,
    VendorDocument,
    VendorInvitation,
    VendorMember,
    VendorPayoutAccount,
    Warehouse,
)
from app.modules.vendor.types import OnboardingStatus


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

    def get_country_for_market(
        self,
        vendor_id: UUID,
        country_id: UUID,
    ) -> VendorCountry | None:
        """Return one organization's application for one market.

        Args:
            vendor_id: Organization identifier.
            country_id: Market identifier.

        Returns:
            Matching country row, or `None`.
        """
        statement = select(VendorCountry).where(
            VendorCountry.vendor_id == vendor_id,
            VendorCountry.country_id == country_id,
        )
        return self.db.scalar(statement)

    def list_countries_for_review(
        self,
        country_id: UUID,
    ) -> list[VendorCountry]:
        """Return submitted and in-review applications for one market.

        Args:
            country_id: Market whose queue is requested.

        Returns:
            Matching country applications.
        """
        statement = select(VendorCountry).where(
            VendorCountry.country_id == country_id,
            VendorCountry.onboarding_status.in_(
                (OnboardingStatus.SUBMITTED, OnboardingStatus.REVIEW),
            ),
        )
        return list(self.db.scalars(statement))

    def add_member(self, member: VendorMember) -> None:
        """Stage one organization membership.

        Args:
            member: Membership to insert.
        """
        self.db.add(member)

    def get_member(
        self,
        vendor_member_id: UUID,
        *,
        for_update: bool = False,
    ) -> VendorMember | None:
        """Return one membership by its identifier.

        Args:
            vendor_member_id: Stable membership identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching membership, or `None`.
        """
        statement = select(VendorMember).where(
            VendorMember.vendor_member_id == vendor_member_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_member_for_user(
        self,
        vendor_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> VendorMember | None:
        """Return one account's membership in one organization.

        Args:
            vendor_id: Organization identifier.
            user_id: Account identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching membership, or `None`.
        """
        statement = select(VendorMember).where(
            VendorMember.vendor_id == vendor_id,
            VendorMember.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def add_document(self, document: VendorDocument) -> None:
        """Stage one document metadata row.

        Args:
            document: Document metadata to insert.
        """
        self.db.add(document)

    def list_documents(self, vendor_country_id: UUID) -> list[VendorDocument]:
        """Return document metadata for one country application.

        Args:
            vendor_country_id: Country application identifier.

        Returns:
            Document metadata rows.
        """
        statement = select(VendorDocument).where(
            VendorDocument.vendor_country_id == vendor_country_id,
        )
        return list(self.db.scalars(statement))

    def get_document(self, vendor_document_id: UUID) -> VendorDocument | None:
        """Return one document metadata row.

        Args:
            vendor_document_id: Document identifier.

        Returns:
            Matching document, or `None`.
        """
        statement = select(VendorDocument).where(
            VendorDocument.vendor_document_id == vendor_document_id,
        )
        return self.db.scalar(statement)

    def add_invitation(self, invitation: VendorInvitation) -> None:
        """Stage one staff invitation.

        Args:
            invitation: Invitation to insert.
        """
        self.db.add(invitation)

    def get_invitation(
        self,
        invitation_id: UUID,
        *,
        for_update: bool = False,
    ) -> VendorInvitation | None:
        """Return one staff invitation.

        Args:
            invitation_id: Invitation identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching invitation, or `None`.
        """
        statement = select(VendorInvitation).where(
            VendorInvitation.invitation_id == invitation_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def find_open_invitation(
        self,
        vendor_id: UUID,
        country_id: UUID,
        email: str,
        now: datetime,
    ) -> VendorInvitation | None:
        """Return an unaccepted invitation for one email.

        Args:
            vendor_id: Organization identifier.
            country_id: Market identifier.
            email: Normalized email address.
            now: Current UTC time.

        Returns:
            Open invitation, or `None`.
        """
        statement = select(VendorInvitation).where(
            VendorInvitation.vendor_id == vendor_id,
            VendorInvitation.country_id == country_id,
            VendorInvitation.email == email,
            VendorInvitation.accepted_at.is_(None),
            VendorInvitation.revoked_at.is_(None),
            VendorInvitation.expires_at > now,
        )
        return self.db.scalar(statement)

    def get_store_for_vendor_country(
        self,
        vendor_id: UUID,
        country_id: UUID,
        *,
        for_update: bool = False,
    ) -> Store | None:
        """Return one organization's store in one country.

        Args:
            vendor_id: Organization identifier.
            country_id: Market identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching store, or `None`.
        """
        statement = select(Store).where(
            Store.vendor_id == vendor_id,
            Store.country_id == country_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def find_store_by_country_slug(self, country_id: UUID, slug: str) -> Store | None:
        """Return the store that already uses a slug in one country.

        Args:
            country_id: Market identifier.
            slug: Normalized store slug.

        Returns:
            Matching store, or `None`.
        """
        statement = select(Store).where(Store.country_id == country_id, Store.slug == slug)
        return self.db.scalar(statement)

    def add_store(self, store: Store) -> None:
        """Stage one storefront.

        Args:
            store: Store to insert.
        """
        self.db.add(store)

    def get_translation(self, store_id: UUID, locale_code: str) -> StoreTranslation | None:
        """Return one store translation.

        Args:
            store_id: Store identifier.
            locale_code: Locale code.

        Returns:
            Matching translation, or `None`.
        """
        statement = select(StoreTranslation).where(
            StoreTranslation.store_id == store_id,
            StoreTranslation.locale_code == locale_code,
        )
        return self.db.scalar(statement)

    def add_translation(self, translation: StoreTranslation) -> None:
        """Stage one store translation.

        Args:
            translation: Translation to insert.
        """
        self.db.add(translation)

    def add_payout(self, account: VendorPayoutAccount) -> None:
        """Stage one payout destination.

        Args:
            account: Payout destination to insert.
        """
        self.db.add(account)

    def get_payout(
        self,
        payout_account_id: UUID,
        *,
        for_update: bool = False,
    ) -> VendorPayoutAccount | None:
        """Return one payout destination.

        Args:
            payout_account_id: Payout destination identifier.
            for_update: Whether to lock the selected row.

        Returns:
            Matching payout destination, or `None`.
        """
        statement = select(VendorPayoutAccount).where(
            VendorPayoutAccount.vendor_payout_account_id == payout_account_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_default_payout(
        self,
        vendor_country_id: UUID,
        currency_code: str,
        *,
        for_update: bool = False,
    ) -> VendorPayoutAccount | None:
        """Return the default payout destination for one currency.

        Args:
            vendor_country_id: Country application identifier.
            currency_code: ISO currency code.
            for_update: Whether to lock the selected row.

        Returns:
            Default payout destination, or `None`.
        """
        statement = select(VendorPayoutAccount).where(
            VendorPayoutAccount.vendor_country_id == vendor_country_id,
            VendorPayoutAccount.currency_code == currency_code,
            VendorPayoutAccount.is_default.is_(True),
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def clear_default_payouts(self, vendor_country_id: UUID, currency_code: str) -> None:
        """Clear the current default payout for one currency.

        Args:
            vendor_country_id: Country application identifier.
            currency_code: ISO currency code.
        """
        statement = (
            select(VendorPayoutAccount)
            .where(
                VendorPayoutAccount.vendor_country_id == vendor_country_id,
                VendorPayoutAccount.currency_code == currency_code,
                VendorPayoutAccount.is_default.is_(True),
            )
            .with_for_update()
        )
        for account in self.db.scalars(statement):
            account.is_default = False
        self.db.flush()

    def add_warehouse(self, warehouse: Warehouse) -> None:
        """Stage one warehouse.

        Args:
            warehouse: Warehouse to insert.
        """
        self.db.add(warehouse)

    def find_warehouse_by_code(self, vendor_id: UUID, code: str) -> Warehouse | None:
        """Return a warehouse that already uses a code for one organization.

        Args:
            vendor_id: Organization identifier.
            code: Warehouse code.

        Returns:
            Matching warehouse, or `None`.
        """
        statement = select(Warehouse).where(
            Warehouse.vendor_id == vendor_id,
            Warehouse.code == code,
        )
        return self.db.scalar(statement)

    def list_warehouses(
        self,
        vendor_id: UUID,
        country_id: UUID,
        *,
        for_update: bool = False,
    ) -> list[Warehouse]:
        """Return warehouses for one organization in one country.

        Args:
            vendor_id: Organization identifier.
            country_id: Market identifier.
            for_update: Whether to lock the selected rows.

        Returns:
            Matching warehouses.
        """
        statement = select(Warehouse).where(
            Warehouse.vendor_id == vendor_id,
            Warehouse.country_id == country_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return list(self.db.scalars(statement))
