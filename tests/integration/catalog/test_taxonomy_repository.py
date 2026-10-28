"""Test taxonomy persistence and country enablement on PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.auth_context import AuthContext
from app.modules.access.catalog import ROLES
from app.modules.access.models import UserRoleAssignment
from app.modules.access.types import ScopeType
from app.modules.access.vendor_grants import AssignmentVendorAuthorizer
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.models import Brand
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.service import CatalogTaxonomyService
from app.modules.catalog.types import RecordStatus, ReviewDecision
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.types import AccountStatus
from app.modules.vendor.models import VendorCountry
from app.modules.vendor.types import OnboardingStatus
from app.modules.catalog.dependencies import VendorApprovedSellerLookup
from tests.integration.vendor.test_vendor_repository import (
    _alembic,
    _create_temporary_database,
    _drop_database,
    _insert_user,
    _insert_vendor,
)

NOW = datetime(2026, 10, 28, tzinfo=UTC)


class Consume:
    """Accept the taxonomy step-up token."""

    def __call__(self, token: str, actor: AuthContext, action_class: str) -> None:
        """Record that a grant was presented."""
        del token, actor, action_class


@pytest.fixture(scope="module")
def taxonomy_database_url() -> Iterator[str]:
    """Upgrade an empty database through the taxonomy permission seed."""
    database_url = _create_temporary_database()
    if database_url is None:
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        _alembic(database_url, "upgrade", "20261028_0035")
        yield database_url
    finally:
        _drop_database(database_url)


@pytest.fixture()
def session(taxonomy_database_url: str) -> Iterator[Session]:
    """Yield a transaction that rolls back after each taxonomy test."""
    engine = create_engine(taxonomy_database_url, poolclass=NullPool)
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


def test_country_enablement_and_brand_link_stay_scoped(session: Session) -> None:
    """Enable Nepal only, and link a repeated brand name to one row."""
    countries = DevelopmentCountryReader()
    nepal = countries.resolve_country("NP").country_id
    owner_id = _insert_user(session)
    reviewer_id = _insert_user(session)
    admin_id = _insert_user(session)
    _activate(session, owner_id)
    _activate(session, reviewer_id)
    _activate(session, admin_id)
    vendor = _insert_vendor(session, owner_id)
    _approved_country(session, vendor.vendor_id, nepal)
    _assign(session, admin_id, "SUPER_ADMIN", None, None)
    _assign(session, reviewer_id, "COUNTRY_OPERATIONS", nepal, None)
    _assign(session, owner_id, "VENDOR_OWNER", nepal, vendor.vendor_id)
    service = _service(session)
    with pytest.raises(CatalogServiceError) as denied:
        service.create_category(
            _actor(owner_id),
            code="shoes",
            parent_id=None,
            locale_code="ne",
            name="Shoes",
        )
    assert denied.value.status_code == 403
    created = service.create_category(
        _actor(admin_id),
        code="Shoes",
        parent_id=None,
        locale_code="ne",
        name="Shoes",
    )
    assert service.list_categories("NP", "ne") == []
    assert service.list_categories("AE", "en") == []
    service.enable_category(
        _actor(reviewer_id),
        created.category_id,
        country_code="NP",
        step_up_token="grant",
    )
    assert [item.code for item in service.list_categories("NP", "ne")] == ["shoes"]
    assert service.list_categories("AE", "en") == []
    _link_repeated_brand(session, service, owner_id, reviewer_id, vendor.vendor_id)


def _link_repeated_brand(session: Session, service, owner_id, reviewer_id, vendor_id) -> None:
    """Link a second spelling to one brand and refuse a retired brand."""
    request = service.request_brand(
        _actor(owner_id),
        vendor_id=vendor_id,
        country_code="NP",
        proposed_name="Nike",
    )
    assert request.brand_id is None
    assert session.scalar(select(Brand).limit(1)) is None
    approved = service.decide_brand(
        _actor(reviewer_id),
        request.request_id,
        decision=ReviewDecision.APPROVED,
        link_brand_id=None,
        step_up_token="grant",
    )
    repeat = service.request_brand(
        _actor(owner_id),
        vendor_id=vendor_id,
        country_code="NP",
        proposed_name="NIKE",
    )
    linked = service.decide_brand(
        _actor(reviewer_id),
        repeat.request_id,
        decision=ReviewDecision.APPROVED,
        link_brand_id=approved.brand_id,
        step_up_token="grant",
    )
    session.flush()
    brands = list(session.scalars(select(Brand)))
    assert linked.brand_id == approved.brand_id
    assert len(brands) == 1
    assert service.list_brands("AE") == []
    brand = brands[0]
    brand.status = RecordStatus.RETIRED
    session.flush()
    retired_request = service.request_brand(
        _actor(owner_id),
        vendor_id=vendor_id,
        country_code="NP",
        proposed_name="Nike",
    )
    with pytest.raises(CatalogServiceError) as retired:
        service.decide_brand(
            _actor(reviewer_id),
            retired_request.request_id,
            decision=ReviewDecision.APPROVED,
            link_brand_id=brand.brand_id,
            step_up_token="grant",
        )
    assert retired.value.code == "BRAND_RETIRED"
    assert session.scalar(text("SELECT count(*) FROM public.products")) == 0
    offer_tables = session.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'catalog' "
            "AND table_name IN ('product', 'sku', 'media_asset', 'seller_offer')"
        )
    )
    assert offer_tables == 0


def _activate(db: Session, user_id) -> None:
    """Mark one inserted account active."""
    from app.modules.identity.models import UserAccount
    account = db.scalar(select(UserAccount).where(UserAccount.user_id == user_id))
    account.status = AccountStatus.ACTIVE
    db.flush()


def _approved_country(db: Session, vendor_id, country_id) -> None:
    """Insert one approved country application."""
    db.add(
        VendorCountry(
            vendor_country_id=uuid4(),
            vendor_id=vendor_id,
            country_id=country_id,
            onboarding_status=OnboardingStatus.APPROVED,
            selling_enabled=False,
            payout_enabled=False,
            version=1,
        )
    )
    db.flush()


def _assign(db: Session, user_id, role_code: str, country_id, vendor_id) -> None:
    """Grant one seeded role."""
    role = next(item for item in ROLES if item.code == role_code)
    if vendor_id is not None:
        scope_type = ScopeType.VENDOR
        scope_id = vendor_id
    elif country_id is not None:
        scope_type = ScopeType.COUNTRY
        scope_id = country_id
    else:
        scope_type = ScopeType.GLOBAL
        scope_id = None
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


def _service(db: Session) -> CatalogTaxonomyService:
    """Build a taxonomy service over the live session."""
    return CatalogTaxonomyService(
        CatalogRepository(db),
        DevelopmentCountryReader(),
        AssignmentVendorAuthorizer(db, lambda: NOW),
        VendorApprovedSellerLookup(db),
        Consume(),
    )


def _actor(user_id) -> AuthContext:
    """Build one authenticated actor."""
    return AuthContext(user_id, uuid4(), 1, ("password",), NOW, uuid4())
