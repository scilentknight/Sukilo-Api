"""Compose process-scoped customer adapters for application startup."""

from fastapi import FastAPI

from app.core.config import Settings
from app.modules.customers.application import CustomerApplicationBuilder
from app.modules.identity.application import IdentityApplicationBuilder


def build_customer_application_factory(
    settings: Settings,
    identity_builder: IdentityApplicationBuilder,
) -> CustomerApplicationBuilder:
    """Build customer composition from the exact identity adapters."""
    production = settings.APP_ENV.casefold() == "production"
    return CustomerApplicationBuilder(
        identity_builder=identity_builder,
        allow_noop_audit=not production,
    )


def configure_customers(app: FastAPI, settings: Settings) -> None:
    """Attach a customer factory sharing identity security adapters."""
    identity_builder = getattr(
        app.state,
        "identity_application_factory",
        None,
    )
    if not isinstance(identity_builder, IdentityApplicationBuilder):
        raise RuntimeError("Identity composition must precede customer composition.")
    app.state.customer_application_factory = build_customer_application_factory(
        settings,
        identity_builder,
    )
