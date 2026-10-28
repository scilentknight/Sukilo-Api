"""Test vendor repository statements and PostgreSQL constraints."""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401

from app.core.config import settings
from app.modules.vendor.models import Vendor, VendorCountry, VendorMember
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import (
    MemberStatus,
    OnboardingStatus,
    VendorStatus,
    VendorType,
    VerificationStatus,
)

ROOT = Path(__file__).resolve().parents[3]
USER_ID = UUID(int=11)
VENDOR_ID = UUID(int=22)
COUNTRY_ROW_ID = UUID(int=33)


def _compiled(statement: object) -> str:
    """Compile one statement with the PostgreSQL dialect."""
    return str(statement.compile(dialect=postgresql.dialect()))


def test_repository_insert_does_not_commit() -> None:
    """Stage organization and country rows without committing."""
    db = MagicMock()
    repository = VendorRepository(db)
    vendor = Vendor(vendor_id=VENDOR_ID, legal_name="Acme", display_name="Acme")
    country = VendorCountry(vendor_country_id=COUNTRY_ROW_ID, vendor_id=VENDOR_ID)
    repository.add_vendor(vendor)
    repository.add_country(country)
    assert db.add.call_args_list[0].args == (vendor,)
    assert db.add.call_args_list[1].args == (country,)
    db.commit.assert_not_called()
    db.flush.assert_not_called()


def test_repository_lookup_uses_vendor_schema_and_row_lock() -> None:
    """Select the vendor schema and lock a row only when requested."""
    db = MagicMock()
    db.scalar.return_value = None
    repository = VendorRepository(db)
    assert repository.get_vendor(VENDOR_ID, for_update=True) is None
    sql = _compiled(db.scalar.call_args.args[0])
    assert "vendor.vendor" in sql
    assert "FOR UPDATE" in sql
    assert "public.vendor" not in sql
    assert repository.get_country(COUNTRY_ROW_ID) is None
    country_sql = _compiled(db.scalar.call_args.args[0])
    assert "vendor.vendor_country" in country_sql
    assert "FOR UPDATE" not in country_sql


def test_country_list_filters_one_vendor() -> None:
    """Load country rows for one organization only."""
    db = MagicMock()
    db.scalars.return_value = []
    rows = VendorRepository(db).list_countries_for_vendor(VENDOR_ID)
    sql = _compiled(db.scalars.call_args.args[0])
    assert rows == []
    assert "vendor.vendor_country" in sql
    assert "vendor_id" in sql
    assert "public.vendor" not in sql


def _configured_database_url() -> str:
    """Return the local application database URL when pytest supplies a dummy."""
    file_url = dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if isinstance(file_url, str) and file_url.strip():
        return file_url
    return settings.DATABASE_URL


def _create_temporary_database() -> str | None:
    """Create an empty database, or return None when PostgreSQL is down."""
    url = make_url(_configured_database_url())
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"connect_timeout": 2},
    )
    name = f"sukilo_vendor_{uuid4().hex[:12]}"
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except OperationalError:
        return None
    finally:
        admin.dispose()
    return url.set(database=name).render_as_string(hide_password=False)


def _drop_database(database_url: str) -> None:
    """Drop one temporary vendor test database."""
    url = make_url(database_url)
    name = url.database or ""
    if not name.startswith("sukilo_vendor_") or not name.removeprefix("sukilo_vendor_").isalnum():
        raise RuntimeError("Refusing to drop a non-temporary database.")
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        admin.dispose()


def _alembic(database_url: str, *args: str) -> None:
    """Run one Alembic command against a temporary database."""
    completed = subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail)


def _vendor_tables(database_url: str) -> set[str]:
    """Return table names in the vendor schema."""
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'vendor'"
                ),
            ).scalars()
            return set(rows)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def upgraded_database_url() -> Iterator[str]:
    """Upgrade an empty database through the vendor foundation."""
    database_url = _create_temporary_database()
    if database_url is None:
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        _alembic(database_url, "upgrade", "20261028_0022")
        yield database_url
    finally:
        _drop_database(database_url)


@pytest.fixture()
def session(upgraded_database_url: str) -> Iterator[Session]:
    """Yield a transaction that rolls back after each constraint test."""
    engine = create_engine(upgraded_database_url, poolclass=NullPool)
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


def test_upgrade_creates_vendor_tables(upgraded_database_url: str) -> None:
    """Create the vendor schema and its three foundation tables."""
    assert _vendor_tables(upgraded_database_url) == {
        "vendor",
        "vendor_country",
        "vendor_member",
    }


def test_downgrade_returns_to_identity_head() -> None:
    """Remove the vendor schema and leave the identity head in place."""
    database_url = _create_temporary_database()
    if database_url is None:
        pytest.skip("Configured PostgreSQL database is unavailable.")
    try:
        _alembic(database_url, "upgrade", "20261028_0022")
        _alembic(database_url, "downgrade", "20261012_0018")
        assert _vendor_tables(database_url) == set()
        engine = create_engine(database_url, poolclass=NullPool)
        try:
            with engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version"),
                ).scalar_one()
        finally:
            engine.dispose()
        assert version == "20261012_0018"
    finally:
        _drop_database(database_url)


def _insert_user(db: Session) -> UUID:
    """Insert one account row required by vendor foreign keys."""
    user_id = uuid4()
    db.execute(
        text("INSERT INTO iam.user_account (user_id) VALUES (:user_id)"),
        {"user_id": user_id},
    )
    return user_id


def _insert_vendor(db: Session, user_id: UUID) -> Vendor:
    """Insert one pending organization owned by the account."""
    vendor = Vendor(
        vendor_id=uuid4(),
        legal_name="Acme Trading",
        display_name="Acme",
        vendor_type=VendorType.COMPANY,
        registration_country_id=uuid4(),
        status=VendorStatus.PENDING,
        created_by_user_id=user_id,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _insert_country(db: Session, vendor_id: UUID, country_id: UUID) -> None:
    """Insert one draft country application."""
    db.add(
        VendorCountry(
            vendor_country_id=uuid4(),
            vendor_id=vendor_id,
            country_id=country_id,
            onboarding_status=OnboardingStatus.DRAFT,
            verification_status=VerificationStatus.PENDING,
            selling_enabled=False,
            payout_enabled=False,
            version=1,
        ),
    )
    db.flush()


def test_same_vendor_and_country_conflict(session: Session) -> None:
    """Reject a second application for the same organization and country."""
    vendor = _insert_vendor(session, _insert_user(session))
    country_id = uuid4()
    _insert_country(session, vendor.vendor_id, country_id)
    with pytest.raises(IntegrityError):
        _insert_country(session, vendor.vendor_id, country_id)


def test_same_vendor_accepts_different_countries(session: Session) -> None:
    """Allow one organization to hold drafts for two countries."""
    vendor = _insert_vendor(session, _insert_user(session))
    _insert_country(session, vendor.vendor_id, uuid4())
    _insert_country(session, vendor.vendor_id, uuid4())
    stored = VendorRepository(session).list_countries_for_vendor(vendor.vendor_id)
    assert len(stored) == 2


def test_duplicate_membership_conflicts(session: Session) -> None:
    """Reject two memberships for the same organization and account."""
    user_id = _insert_user(session)
    vendor = _insert_vendor(session, user_id)
    session.add(
        VendorMember(
            vendor_member_id=uuid4(),
            vendor_id=vendor.vendor_id,
            user_id=user_id,
            status=MemberStatus.INVITED,
        ),
    )
    session.flush()
    session.add(
        VendorMember(
            vendor_member_id=uuid4(),
            vendor_id=vendor.vendor_id,
            user_id=user_id,
            status=MemberStatus.INVITED,
        ),
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_active_membership_without_acceptance_is_rejected(session: Session) -> None:
    """Reject an active membership that has no acceptance time."""
    user_id = _insert_user(session)
    vendor = _insert_vendor(session, user_id)
    session.add(
        VendorMember(
            vendor_member_id=uuid4(),
            vendor_id=vendor.vendor_id,
            user_id=user_id,
            status=MemberStatus.ACTIVE,
            accepted_at=None,
        ),
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_creator_account_is_restricted(session: Session) -> None:
    """Keep the account that created an organization."""
    user_id = _insert_user(session)
    _insert_vendor(session, user_id)
    with pytest.raises(IntegrityError):
        session.execute(
            text("DELETE FROM iam.user_account WHERE user_id = :user_id"),
            {"user_id": user_id},
        )


def test_deleting_vendor_with_country_is_restricted(session: Session) -> None:
    """Keep an organization that still has a country application."""
    vendor = _insert_vendor(session, _insert_user(session))
    _insert_country(session, vendor.vendor_id, uuid4())
    session.delete(vendor)
    with pytest.raises(IntegrityError):
        session.flush()
