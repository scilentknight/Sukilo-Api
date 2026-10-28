"""Load validated application and authentication settings."""

from email.utils import formataddr
from typing import Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configure application infrastructure and security policy."""

    APP_NAME: str = "Sukilo API"
    APP_ENV: str = "development"
    DEBUG: bool = True
    DATABASE_URL: str
    FRONTEND_URL: str = "http://localhost:3000"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "product_images"
    DINOV2_MODEL: str = "dinov2_vitl14"
    ENABLE_VECTOR_STACK: bool = True
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_MB: int = 10
    SEARCH_MIN_SIMILARITY: float = 0.50
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.1.65:3000",
    ]
    JWT_ISSUER: str = "http://localhost:8000"
    JWT_AUDIENCE: str = "sukilo-api"
    JWT_PRIVATE_KEY_REFERENCE: str | None = None
    JWT_PUBLIC_KEY_SET: dict[str, str] = Field(default_factory=dict)
    JWT_ACTIVE_KEY_ID: str | None = None
    ACCESS_TOKEN_TTL_SECONDS: int = 600
    REFRESH_TOKEN_TTL_SECONDS: int = 2_592_000
    OTP_TTL_SECONDS: int = 300
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_COOLDOWN_SECONDS: int = 60
    PASSWORD_RESET_TTL_SECONDS: int = 600
    AUTH_DIGEST_PEPPER_REFERENCE: str | None = None
    REDIS_URL: str | None = None
    SMTP_PROVIDER_REFERENCE: str | None = None
    SMTP_HOST: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SMTP_HOST", "MAIL_HOST"),
    )
    SMTP_PORT: int = Field(
        default=587,
        validation_alias=AliasChoices("SMTP_PORT", "MAIL_PORT"),
    )
    SMTP_USERNAME: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SMTP_USERNAME", "MAIL_USERNAME"),
    )
    SMTP_PASSWORD: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SMTP_PASSWORD", "MAIL_PASSWORD"),
    )
    SMTP_FROM: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SMTP_FROM", "MAIL_FROM_ADDRESS"),
    )
    SMTP_STARTTLS: bool = True
    SMTP_USE_SSL: bool = False
    MAIL_ENCRYPTION: str | None = None
    MAIL_FROM_NAME: str | None = None
    REQUIRE_EMAIL_VERIFICATION: bool = True
    AUTH_COOKIE_SECURE: bool = True
    AUTH_COOKIE_SAMESITE: str = "lax"
    AUTH_COOKIE_NAME: str = "sukilo_refresh"
    AUTH_CSRF_COOKIE_NAME: str = "sukilo_csrf"
    PAYOUT_ENCRYPTION_KEY: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @model_validator(mode="after")
    def apply_mail_settings(self) -> Self:
        """Map MAIL_ENCRYPTION and the display name onto the SMTP sender."""
        encryption = (self.MAIL_ENCRYPTION or "").strip().casefold()
        if encryption in {"ssl", "smtps"}:
            self.SMTP_STARTTLS = False
            self.SMTP_USE_SSL = True
        elif encryption in {"tls", "starttls"}:
            self.SMTP_STARTTLS = True
            self.SMTP_USE_SSL = False
        if self.SMTP_PASSWORD:
            self.SMTP_PASSWORD = self.SMTP_PASSWORD.replace(" ", "")
        name = (self.MAIL_FROM_NAME or "").strip()
        address = (self.SMTP_FROM or "").strip()
        if name and address and "<" not in address:
            self.SMTP_FROM = formataddr((name, address))
        return self

    @model_validator(mode="after")
    def validate_production_auth(self) -> Self:
        """Reject production startup without authentication infrastructure."""
        if self.APP_ENV.casefold() != "production":
            return self
        required = {
            "JWT_PRIVATE_KEY_REFERENCE": self.JWT_PRIVATE_KEY_REFERENCE,
            "JWT_ACTIVE_KEY_ID": self.JWT_ACTIVE_KEY_ID,
            "AUTH_DIGEST_PEPPER_REFERENCE": self.AUTH_DIGEST_PEPPER_REFERENCE,
            "REDIS_URL": self.REDIS_URL,
            "SMTP_PROVIDER_REFERENCE": self.SMTP_PROVIDER_REFERENCE,
        }
        missing = [name for name, value in required.items() if not value]
        if not self.JWT_PUBLIC_KEY_SET:
            missing.append("JWT_PUBLIC_KEY_SET")
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"Production authentication is not configured: {fields}")
        return self


settings = Settings()
