"""Compose process-scoped access-control adapters."""

from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI
from redis import Redis

from app.core.config import Settings
from app.modules.access.application import AccessApplicationBuilder
from app.modules.access.cache import CacheBackend, InMemoryCacheBackend
from app.modules.identity.application import IdentityApplicationBuilder


def build_access_application_factory(
    settings: Settings,
    identity_builder: IdentityApplicationBuilder,
) -> AccessApplicationBuilder:
    """Build access composition from the exact identity adapters."""
    clock = identity_builder.clock
    cache_backend = _build_cache_backend(settings, clock)
    production = settings.APP_ENV.casefold() == "production"
    return AccessApplicationBuilder(
        digests=identity_builder.digests,
        countries=identity_builder.countries,
        audit_writer=identity_builder.audit_writer,
        cache_backend=cache_backend,
        clock=clock,
        allow_noop_audit=not production,
    )


def configure_access(app: FastAPI, settings: Settings) -> None:
    """Attach an access factory sharing identity security adapters."""
    identity_builder = getattr(
        app.state,
        "identity_application_factory",
        None,
    )
    if not isinstance(identity_builder, IdentityApplicationBuilder):
        raise RuntimeError("Identity composition must precede access composition.")
    app.state.access_application_factory = build_access_application_factory(
        settings,
        identity_builder,
    )


def _build_cache_backend(
    settings: Settings,
    clock: Callable[[], datetime],
) -> CacheBackend:
    if settings.REDIS_URL:
        return Redis.from_url(settings.REDIS_URL)
    if settings.APP_ENV.casefold() == "production":
        raise ValueError("REDIS_URL is required for production access caching.")
    return InMemoryCacheBackend(clock)
