"""Test identity application composition at process startup."""

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.modules.identity.application import IdentityApplication
from app.modules.identity.bootstrap import (
    DevelopmentCountryReader,
    build_identity_application_factory,
    configure_identity,
)


class DatabaseFake:
    """Stand in for a SQLAlchemy session during composition tests."""

    def add(self, value) -> None:
        """Ignore ORM staging."""
        del value


def test_configure_identity_attaches_request_scoped_factory() -> None:
    """Wire a factory that builds identity services for one database session."""
    app = FastAPI()
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
    )
    configure_identity(app, settings)
    factory = app.state.identity_application_factory
    assert factory is not None
    application = factory(DatabaseFake())
    assert isinstance(application, IdentityApplication)


def test_build_identity_factory_uses_ephemeral_keys_outside_production() -> None:
    """Allow local development without configured JWT or pepper secrets."""
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
        JWT_PRIVATE_KEY_REFERENCE=None,
        JWT_ACTIVE_KEY_ID=None,
        AUTH_DIGEST_PEPPER_REFERENCE=None,
        REDIS_URL=None,
    )
    factory = build_identity_application_factory(settings)
    application = factory(DatabaseFake())
    assert application.tokens.active_key_id == "dev"


def test_production_identity_requires_durable_country_adapter() -> None:
    """Refuse to substitute the development country reader in production."""
    development = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
    )
    settings = development.model_copy(update={"APP_ENV": "production"})
    with pytest.raises(ValueError, match="country adapter"):
        build_identity_application_factory(settings, audit_writer=object())


def test_development_country_reader_enables_assignment_management() -> None:
    """Allow local testing of country-scoped assignment administration."""
    reader = DevelopmentCountryReader()
    country = reader.resolve_country("NP")
    assert reader.is_operation_enabled(
        country.country_id,
        "iam.assignment.manage",
    )
