"""Test approval rollback and country denial on PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core.auth_context import AuthContext
from app.core.transactions import DatabaseTransaction
from app.modules.access.catalog import ROLES
from app.modules.access.models import Role, UserRoleAssignment
from app.modules.access.types import ScopeType
from app.modules.access.vendor_grants import (
    AssignmentVendorAuthorizer,
    SessionVendorRoleGranter,
    VendorGrantError,
)
from app.modules.identity.bootstrap import DevelopmentCountryReader
from app.modules.identity.types import AccountStatus
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.models import VendorCountry, VendorMember
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.reviews import VendorReviewService
from app.modules.vendor.types import OnboardingStatus
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
    """Record audit events staged in the approval transaction."""

    def __init__(self) -> None:
        """Initialize an empty event list."""
        self.events: list[AuditEventInput] = []

    def stage(self, event: AuditEventInput, transaction: DatabaseTransaction) -> None:
        """Record one event."""
        del transaction
        self.events.append(event)


class Accounts:
    """Read accounts through the active session."""

    def __init__(self, db: Session) -> None:
        """Initialize the session."""
        self.db = db

    def get_by_id(self, user_id, *, for_update: bool = False):
        """Return one account."""
        from app.modules.identity.models import UserAccount
        statement = select(UserAccount).where(UserAccount.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def get_by_email(self, email: str):
        """Return no email match."""
        del email
        return None


@pytest.fixture(scope="module")
def review_database_url() -> Iterator[str]:
    """Upgrade an empty database through the vendor review seed."""
    database_url = _create_temporary_database()
    if database_url is None:
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        _alembic(database_url, "upgrade", "20261028_0025")
        yield database_url
    finally:
        _drop_database(database_url)


@pytest.fixture()
def session(review_database_url: str) -> Iterator[Session]:
    """Yield a transaction that rolls back after each review test."""
    engine = create_engine(review_database_url, poolclass=NullPool)
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


def _activate(db: Session, user_id) -> None:
    """Mark one inserted account active."""
    account = Accounts(db).get_by_id(user_id)
    account.status = AccountStatus.ACTIVE
    db.flush()


def _country(db: Session, vendor_id, country_id) -> VendorCountry:
    """Insert one submitted country application."""
    row = VendorCountry(
        vendor_country_id=uuid4(),
        vendor_id=vendor_id,
        country_id=country_id,
        onboarding_status=OnboardingStatus.SUBMITTED,
        selling_enabled=False,
        payout_enabled=False,
        version=1,
    )
    db.add(row)
    db.flush()
    return row


def _reviewer_assignment(db: Session, reviewer_id, country_id) -> None:
    """Grant country operations for one market."""
    role = next(item for item in ROLES if item.code == "COUNTRY_OPERATIONS")
    db.add(
        UserRoleAssignment(
            assignment_id=uuid4(),
            user_id=reviewer_id,
            role_id=role.role_id,
            scope_type=ScopeType.COUNTRY,
            scope_id=country_id,
            country_id=country_id,
            created_by=reviewer_id,
            version=1,
        )
    )
    db.flush()


def _service(db: Session, grants) -> VendorReviewService:
    """Build a review service over the live session."""
    return VendorReviewService(
        VendorRepository(db),
        Accounts(db),
        DevelopmentCountryReader(),
        AssignmentVendorAuthorizer(db, lambda: NOW),
        grants,
        RecordingAudit(),
        DatabaseTransaction(db),
        lambda: NOW,
        lambda token, actor, action_class: None,
    )


def test_nepal_reviewer_cannot_approve_uae_on_postgres(session: Session) -> None:
    """Keep a UAE application submitted when the reviewer is assigned to Nepal."""
    countries = DevelopmentCountryReader()
    nepal = countries.resolve_country("NP").country_id
    uae = countries.resolve_country("AE").country_id
    applicant_id = _insert_user(session)
    reviewer_id = _insert_user(session)
    _activate(session, applicant_id)
    _activate(session, reviewer_id)
    vendor = _insert_vendor(session, applicant_id)
    country = _country(session, vendor.vendor_id, uae)
    _reviewer_assignment(session, reviewer_id, nepal)
    reviewer = AuthContext(reviewer_id, uuid4(), 1, ("password", "otp"), NOW, uuid4())
    service = _service(session, SessionVendorRoleGranter(session))
    with pytest.raises(VendorServiceError) as captured:
        service.approve(reviewer, country.vendor_country_id, step_up_token="grant")
    assert captured.value.status_code == 403
    assert country.onboarding_status is OnboardingStatus.SUBMITTED
    assert session.scalar(select(func.count()).select_from(VendorMember)) == 0


def test_approval_rollback_removes_member_and_badge(session: Session) -> None:
    """Roll back the owner membership when the badge grant fails."""
    nepal = DevelopmentCountryReader().resolve_country("NP").country_id
    applicant_id = _insert_user(session)
    reviewer_id = _insert_user(session)
    _activate(session, applicant_id)
    _activate(session, reviewer_id)
    vendor = _insert_vendor(session, applicant_id)
    country = _country(session, vendor.vendor_id, nepal)
    _reviewer_assignment(session, reviewer_id, nepal)
    reviewer = AuthContext(reviewer_id, uuid4(), 1, ("password", "otp"), NOW, uuid4())

    class FailingGranter(SessionVendorRoleGranter):
        """Fail before a badge row is inserted."""

        def grant_vendor_owner(self, *args, **kwargs):
            """Raise instead of granting the owner badge."""
            del args, kwargs
            raise VendorGrantError("grant failed")

    service = _service(session, FailingGranter(session))
    with pytest.raises(VendorGrantError):
        with session.begin_nested():
            service.approve(reviewer, country.vendor_country_id, step_up_token="grant")
    assert session.scalar(select(func.count()).select_from(VendorMember)) == 0
    assert country.onboarding_status is OnboardingStatus.SUBMITTED


def test_approval_persists_owner_without_enabling_selling(session: Session) -> None:
    """Store the owner badge and leave selling disabled."""
    nepal = DevelopmentCountryReader().resolve_country("NP").country_id
    applicant_id = _insert_user(session)
    reviewer_id = _insert_user(session)
    _activate(session, applicant_id)
    _activate(session, reviewer_id)
    vendor = _insert_vendor(session, applicant_id)
    country = _country(session, vendor.vendor_id, nepal)
    _reviewer_assignment(session, reviewer_id, nepal)
    reviewer = AuthContext(reviewer_id, uuid4(), 1, ("password", "otp"), NOW, uuid4())
    service = _service(session, SessionVendorRoleGranter(session))
    view = service.approve(reviewer, country.vendor_country_id, step_up_token="grant")
    session.flush()
    owner_role = session.scalar(select(Role).where(Role.code == "VENDOR_OWNER"))
    assignment = session.scalar(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == applicant_id,
            UserRoleAssignment.role_id == owner_role.role_id,
        )
    )
    assert view.selling_enabled is False
    assert view.onboarding_status is OnboardingStatus.APPROVED
    assert assignment is not None
    assert assignment.country_id == nepal
    assert assignment.vendor_id == vendor.vendor_id
    assert session.scalar(select(func.count()).select_from(VendorMember)) == 1
