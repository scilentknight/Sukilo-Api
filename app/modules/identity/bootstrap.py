"""Compose process-scoped identity adapters for application startup."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from redis import Redis

from app.core.config import Settings
from app.core.security import DigestService
from app.modules.identity.application import IdentityApplicationBuilder
from app.modules.identity.notifications import (
    NotificationSender,
    OtpNotification,
    SecurityNotification,
)
from app.modules.identity.ports import IdentityApplicationFactory, SecretResolver
from app.modules.identity.rate_limits import InMemoryRateLimiter, RedisRateLimiter
from app.modules.identity.tokens import TokenService
from app.ports.audit import AuditWriter, NoOpAuditWriter
from app.ports.country import AddressPolicy, CountryReader, CountryReference

logger = logging.getLogger(__name__)


class EnvSecretResolver:
    """Resolve env:, file:, or literal secret references."""

    def resolve(self, reference: str) -> str:
        """Resolve one secret reference without logging its value.

        Args:
            reference: Secret reference or development literal.

        Returns:
            Resolved secret material.

        Raises:
            ValueError: If the referenced secret is missing.
        """
        if reference.startswith("env:"):
            value = os.environ.get(reference.removeprefix("env:"))
            if not value:
                raise ValueError("Referenced environment secret is missing.")
            return value
        if reference.startswith("file:"):
            path = Path(reference.removeprefix("file:"))
            if not path.is_file():
                raise ValueError("Referenced secret file is missing.")
            return path.read_text(encoding="utf-8")
        return reference


class LoggingNotificationSender:
    """Queue authentication notifications without logging secrets."""

    def send_otp(self, notification: OtpNotification) -> None:
        """Record an OTP delivery intent without the code.

        Args:
            notification: OTP notification payload.
        """
        logger.info(
            "otp_notification_queued purpose=%s channel=%s",
            notification.purpose.value,
            notification.channel.value,
        )

    def send_security(self, notification: SecurityNotification) -> None:
        """Record a security notification without destination data.

        Args:
            notification: Security notification payload.
        """
        logger.info(
            "security_notification_queued event=%s",
            notification.event_code,
        )


class DevelopmentCountryReader:
    """Allow registration and login for any ISO alpha-2 market in development."""

    def resolve_country(self, code: str) -> CountryReference:
        """Resolve a country code to a deterministic reference.

        Args:
            code: ISO alpha-2 country code.

        Returns:
            Stable country reference for the supplied code.
        """
        normalized = code.upper()
        return CountryReference(
            country_id=UUID(bytes=normalized.encode("ascii").ljust(16, b"\0")),
            code=normalized,
            status="ACTIVE",
        )

    def is_operation_enabled(self, country_id: UUID, operation: str) -> bool:
        """Enable authentication operations in development.

        Args:
            country_id: Stable country identifier.
            operation: Operation code.

        Returns:
            Whether the operation is enabled.
        """
        del country_id
        return operation in {
            "REGISTER",
            "LOGIN",
            "RESET",
            "iam.assignment.manage",
        }

    def is_locale_enabled(self, country_id: UUID, locale_code: str) -> bool:
        """Allow any locale during development.

        Args:
            country_id: Stable country identifier.
            locale_code: Locale code.

        Returns:
            Always `True` in development.
        """
        del country_id, locale_code
        return True

    def is_currency_enabled(self, country_id: UUID, currency_code: str) -> bool:
        """Allow any currency during development.

        Args:
            country_id: Stable country identifier.
            currency_code: ISO currency code.

        Returns:
            Always `True` in development.
        """
        del country_id, currency_code
        return True

    def geography_belongs_to_country(
        self,
        country_id: UUID,
        geography_area_id: UUID,
    ) -> bool:
        """Treat every geography as belonging to the country in development.

        Args:
            country_id: Stable country identifier.
            geography_area_id: Stable geography identifier.

        Returns:
            Always `True` in development.
        """
        del country_id, geography_area_id
        return True

    def get_address_policy(self, country_id: UUID) -> AddressPolicy:
        """Return a permissive address policy.

        Args:
            country_id: Stable country identifier.

        Returns:
            Development address policy.
        """
        del country_id
        return AddressPolicy(
            postal_code_required=False,
            geography_required=False,
            coordinates_allowed=True,
        )

    def get_active_policy_version(
        self,
        country_id: UUID,
        consent_type: str,
    ) -> str:
        """Return a fixed policy version for development.

        Args:
            country_id: Stable country identifier.
            consent_type: Consent type code.

        Returns:
            Fixed policy version string.
        """
        del country_id, consent_type
        return "dev-1"


def build_identity_application_factory(
    settings: Settings,
    *,
    secrets: SecretResolver | None = None,
    countries: CountryReader | None = None,
    notifications: NotificationSender | None = None,
    audit_writer: AuditWriter | None = None,
) -> IdentityApplicationFactory:
    """Build the process-scoped identity application factory.

    Args:
        settings: Validated application settings.
        secrets: Optional secret resolver override.
        countries: Optional country reader override.
        notifications: Optional notification sender override.
        audit_writer: Optional audit writer override.

    Returns:
        Factory that builds request-scoped identity services.

    Raises:
        ValueError: If production authentication dependencies are incomplete.
    """
    production = settings.APP_ENV.casefold() == "production"
    if production and countries is None:
        raise ValueError("A durable country adapter is required in production.")
    resolver = secrets or EnvSecretResolver()
    digests = _build_digest_service(settings, resolver, production=production)
    tokens = _build_token_service(settings, resolver, production=production)
    rate_limiter = _build_rate_limiter(settings, production=production)
    allow_noop_audit = not production
    return IdentityApplicationBuilder(
        digests=digests,
        tokens=tokens,
        countries=countries or DevelopmentCountryReader(),
        notifications=notifications or LoggingNotificationSender(),
        rate_limiter=rate_limiter,
        audit_writer=audit_writer or NoOpAuditWriter(),
        clock=lambda: datetime.now(UTC),
        refresh_ttl=_seconds(settings.REFRESH_TOKEN_TTL_SECONDS),
        otp_ttl=_seconds(settings.OTP_TTL_SECONDS),
        reset_ttl=_seconds(settings.PASSWORD_RESET_TTL_SECONDS),
        otp_max_attempts=settings.OTP_MAX_ATTEMPTS,
        allow_noop_audit=allow_noop_audit,
    )


def configure_identity(app: FastAPI, settings: Settings) -> None:
    """Attach the identity application factory to FastAPI state.

    Args:
        app: FastAPI application instance.
        settings: Validated application settings.
    """
    app.state.identity_application_factory = build_identity_application_factory(
        settings
    )
    logger.info("Identity authentication stack initialized.")


def _build_digest_service(
    settings: Settings,
    resolver: SecretResolver,
    *,
    production: bool,
) -> DigestService:
    reference = settings.AUTH_DIGEST_PEPPER_REFERENCE
    if reference:
        pepper = resolver.resolve(reference).encode("utf-8")
        return DigestService(active_key_id="v1", peppers={"v1": pepper})
    if production:
        raise ValueError("AUTH_DIGEST_PEPPER_REFERENCE is required in production.")
    logger.warning("Using ephemeral AUTH digest pepper for non-production.")
    return DigestService(
        active_key_id="dev",
        peppers={"dev": os.urandom(32)},
    )


def _build_token_service(
    settings: Settings,
    resolver: SecretResolver,
    *,
    production: bool,
) -> TokenService:
    if (
        settings.JWT_PRIVATE_KEY_REFERENCE
        and settings.JWT_ACTIVE_KEY_ID
        and settings.JWT_PUBLIC_KEY_SET
    ):
        private_key = resolver.resolve(settings.JWT_PRIVATE_KEY_REFERENCE)
        return TokenService(
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            active_key_id=settings.JWT_ACTIVE_KEY_ID,
            private_key=private_key,
            public_keys=settings.JWT_PUBLIC_KEY_SET,
            access_ttl=_seconds(settings.ACCESS_TOKEN_TTL_SECONDS),
            clock=lambda: datetime.now(UTC),
        )
    if production:
        raise ValueError("JWT signing configuration is required in production.")
    logger.warning("Using ephemeral JWT signing keys for non-production.")
    private_pem, public_pem = _generate_rsa_keypair()
    key_id = settings.JWT_ACTIVE_KEY_ID or "dev"
    return TokenService(
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        active_key_id=key_id,
        private_key=private_pem,
        public_keys={key_id: public_pem},
        access_ttl=_seconds(settings.ACCESS_TOKEN_TTL_SECONDS),
        clock=lambda: datetime.now(UTC),
    )


def _build_rate_limiter(settings: Settings, *, production: bool):
    if settings.REDIS_URL:
        return RedisRateLimiter(Redis.from_url(settings.REDIS_URL))
    if production:
        raise ValueError("REDIS_URL is required in production.")
    logger.warning("Using in-memory authentication rate limits for non-production.")
    return InMemoryRateLimiter(clock=lambda: datetime.now(UTC))


def _generate_rsa_keypair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


def _seconds(value: int) -> timedelta:
    """Convert whole seconds into a timedelta.

    Args:
        value: Duration in seconds.

    Returns:
        Matching timedelta value.
    """
    return timedelta(seconds=value)
