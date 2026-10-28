"""Test store activation and shop constraints on PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.access.catalog import ROLES
from app.modules.access.models import UserRoleAssignment
from app.modules.access.types import ScopeType
from app.modules.access.vendor_grants import AssignmentVendorAuthorizer
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.types import AccountStatus
from app.modules.vendor.account_cipher import FernetAccountCipher
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import Store, VendorCountry, VendorPayoutAccount
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.storefront import VendorStorefrontService
from app.modules.vendor.types import AccountType, OnboardingStatus, StorefrontStatus
from app.ports.audit import AuditEventInput
from tests.integration.vendor.test_vendor_repository import (
    _alembic,
    _create_temporary_database,
    _drop_database,
    _insert_user,
    _insert_vendor,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)


class RecordingAudit:
    """Record audit events staged during store activation."""

    def __init__(self) -> None:
        """Initialize an empty event list."""
        self.events: list[AuditEventInput] = []

    def stage(self, event: AuditEventInput, transaction: DatabaseTransaction) -> None:
        """Record one event."""
        del transaction
        self.events.append(event)


@pytest.fixture(scope="module")
def store_database_url() -> Iterator[str]:
    """Upgrade an empty database through the store permission seed."""
    database_url = _create_temporary_database()
    if database_url is None:
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        _alembic(database_url, "upgrade", "20261028_0030")
        yield database_url
    finally:
        _drop_database(database_url)


@pytest.fixture()
def session(store_database_url: str) -> Iterator[Session]:
    """Yield a transaction that rolls back after each store test."""
    engine = create_engine(store_database_url, poolclass=NullPool)
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
        engine.dispose()


def test_activation_enables_only_the_checked_country(session: Session) -> None:
    """Enable selling in Nepal and leave the other country disabled."""
    countries = DevelopmentCountryReader()
    nepal = countries.resolve_country("NP").country_id
    uae = countries.resolve_country("AE").country_id
    owner_id = _insert_user(session)
    reviewer_id = _insert_user(session)
    _activate(session, owner_id)
    _activate(session, reviewer_id)
    vendor = _insert_vendor(session, owner_id)
    nepal_country = _approved_country(session, vendor.vendor_id, nepal)
    uae_country = _approved_country(session, vendor.vendor_id, uae)
    _assign(session, owner_id, "VENDOR_OWNER", nepal, vendor.vendor_id)
    _assign(session, reviewer_id, "COUNTRY_OPERATIONS", nepal, None)
    service = _service(session)
    owner = _actor(owner_id)
    service.save_store(
        owner,
        vendor_id=vendor.vendor_id,
        country_code="NP",
        slug="Acme Store",
        locale_code="ne",
        name="Acme",
        description=None,
        seo_title=None,
        seo_description=None,
    )
    payout = service.save_payout(
        owner,
        vendor_id=vendor.vendor_id,
        country_code="NP",
        account_type=AccountType.BANK,
        currency_code="NPR",
        account_holder_name="Acme Trading",
        account_number="998877665544",
        bank_name=None,
        is_default=True,
    )
    service.add_warehouse(
        owner,
        vendor_id=vendor.vendor_id,
        country_code="NP",
        code="KTM-1",
        address_line_1="1 Market",
        city_text="Kathmandu",
        postal_code="44600",
        contact_name="Asha",
        contact_phone="9800000000",
        fulfillment_enabled=True,
        pickup_enabled=False,
        status=StorefrontStatus.ACTIVE,
    )
    with pytest.raises(VendorServiceError) as captured:
        service.activate(owner, vendor_id=vendor.vendor_id, country_code="NP")
    assert captured.value.code == "PAYOUT_NOT_VERIFIED"
    assert nepal_country.selling_enabled is False
    with pytest.raises(VendorServiceError) as denied:
        service.verify_payout(owner, payout.vendor_payout_account_id)
    assert denied.value.status_code == 403
    service.verify_payout(_actor(reviewer_id), payout.vendor_payout_account_id)
    view = service.activate(owner, vendor_id=vendor.vendor_id, country_code="NP")
    session.flush()
    assert view.selling_enabled is True
    assert view.payout_enabled is True
    assert view.status is StorefrontStatus.ACTIVE
    assert uae_country.selling_enabled is False
    assert session.scalar(text("SELECT count(*) FROM public.products")) == 0
    catalog_tables = session.scalar(
        text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'catalog'")
    )
    assert catalog_tables == 0
    secret = session.scalar(
        select(VendorPayoutAccount.account_number_encrypted).where(
            VendorPayoutAccount.vendor_payout_account_id == payout.vendor_payout_account_id
        )
    )
    assert secret is not None
    assert "998877665544" not in secret
    assert all("998877665544" not in str(event) for event in service.audit.events)


def test_country_slug_and_default_payout_are_unique(session: Session) -> None:
    """Reject a repeated country slug and a second default payout."""
    nepal = DevelopmentCountryReader().resolve_country("NP").country_id
    owner_id = _insert_user(session)
    vendor = _insert_vendor(session, owner_id)
    other = _insert_vendor(session, owner_id)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(_store(vendor.vendor_id, nepal, "acme"))
            session.add(_store(other.vendor_id, nepal, "acme"))
            session.flush()
    country = _approved_country(session, vendor.vendor_id, nepal)
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            _default_payout(session, country.vendor_country_id)
            _default_payout(session, country.vendor_country_id)
            session.flush()


def _store(vendor_id, country_id, slug: str) -> Store:
    """Build one draft store row."""
    return Store(
        store_id=uuid4(),
        vendor_id=vendor_id,
        country_id=country_id,
        slug=slug,
        status=StorefrontStatus.DRAFT,
        version=1,
    )


def _activate(db: Session, user_id) -> None:
    """Mark one inserted account active."""
    from app.modules.identity.models import UserAccount
    account = db.scalar(select(UserAccount).where(UserAccount.user_id == user_id))
    account.status = AccountStatus.ACTIVE
    db.flush()


def _approved_country(db: Session, vendor_id, country_id) -> VendorCountry:
    """Insert one approved country application."""
    row = VendorCountry(
        vendor_country_id=uuid4(),
        vendor_id=vendor_id,
        country_id=country_id,
        onboarding_status=OnboardingStatus.APPROVED,
        selling_enabled=False,
        payout_enabled=False,
        version=1,
    )
    db.add(row)
    db.flush()
    return row


def _assign(db: Session, user_id, role_code: str, country_id, vendor_id) -> None:
    """Grant one seeded role in a country or vendor scope."""
    role = next(item for item in ROLES if item.code == role_code)
    scope_type = ScopeType.VENDOR if vendor_id is not None else ScopeType.COUNTRY
    scope_id = vendor_id if vendor_id is not None else country_id
    db.add(
        UserRoleAssignment(
            assignment_id=uuid4(),
            user_id=user_id,
            role_id=role.role_id,
            scope_type=scope_type,
            scope_id=scope_id,
            country_id=country_id,
            vendor_id=vendor_id,
            created_by=user_id,
            version=1,
        )
    )
    db.flush()


def _service(db: Session) -> VendorStorefrontService:
    """Build a storefront service over the live session."""
    return VendorStorefrontService(
        VendorRepository(db),
        DevelopmentCountryReader(),
        AssignmentVendorAuthorizer(db, lambda: NOW),
        FernetAccountCipher(Fernet.generate_key().decode("ascii")),
        RecordingAudit(),
        DatabaseTransaction(db),
        lambda: NOW,
    )


def _actor(user_id) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())


def _default_payout(db: Session, vendor_country_id) -> None:
    """Insert one default payout row."""
    db.add(
        VendorPayoutAccount(
            vendor_payout_account_id=uuid4(),
            vendor_country_id=vendor_country_id,
            account_type=AccountType.BANK,
            currency_code="NPR",
            account_holder_name="Acme Trading",
            account_number_masked="****5544",
            account_number_encrypted="ciphertext-value",
            is_default=True,
        )
    )
