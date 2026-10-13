"""Test customer application composition at process startup."""

from fastapi import FastAPI

from app.core.config import Settings
from app.modules.customers.application import CustomerApplication
from app.modules.customers.bootstrap import configure_customers
from app.modules.identity.bootstrap import configure_identity


class DatabaseFake:
    """Stand in for a SQLAlchemy session during composition tests."""

    def add(self, value) -> None:
        """Ignore ORM staging."""
        del value


def test_configure_customers_attaches_request_scoped_factory() -> None:
    """Wire a factory that builds customer services for one database session."""
    app = FastAPI()
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
    )
    configure_identity(app, settings)
    configure_customers(app, settings)
    factory = app.state.customer_application_factory
    assert factory is not None
    application = factory(DatabaseFake())
    assert isinstance(application, CustomerApplication)
