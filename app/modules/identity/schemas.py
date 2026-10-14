"""Define public authentication request and response contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.modules.identity.types import OtpPurpose


class RegisterRequest(BaseModel):
    """Validate first-party registration input."""

    email: EmailStr
    phone_e164: str | None = None
    password: str = Field(min_length=12, max_length=128)
    country_code: str = Field(min_length=2, max_length=2)
    locale_code: str = Field(min_length=2, max_length=32)


class OtpRequest(BaseModel):
    """Validate an OTP request."""

    identifier: str = Field(min_length=3, max_length=320)
    purpose: OtpPurpose
    country_code: str = Field(min_length=2, max_length=2)
    locale_code: str = Field(min_length=2, max_length=32)


class OtpVerifyRequest(BaseModel):
    """Validate an OTP verification attempt."""

    challenge_id: UUID
    purpose: OtpPurpose
    code: str = Field(pattern=r"^\d{6}$")
    action_class: str | None = Field(default=None, max_length=64)


class ChallengeAcceptedResponse(BaseModel):
    """Return an opaque challenge and generic delivery status.

    `dev_code` is present only in development when no SMTP server is configured.
    Production responses omit it.
    """

    challenge_id: UUID
    message: str = "If eligible, a verification message was sent."
    dev_code: str | None = None


class OtpVerifyResponse(BaseModel):
    """Return the purpose-specific OTP result."""

    verified: bool = True
    grant: str | None = None


class LoginRequest(BaseModel):
    """Validate password login input."""

    identifier: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    country_code: str = Field(min_length=2, max_length=2)
    device_id: str | None = Field(default=None, max_length=128)
    device_name: str | None = Field(default=None, max_length=160)


class AccessTokenResponse(BaseModel):
    """Return a short-lived bearer token and the session's CSRF value.

    The CSRF cookie is scoped to the API origin, so cross-origin browser
    clients cannot read it; they echo this value in `X-CSRF-Token` instead.
    """

    access_token: str
    token_type: str = "bearer"
    csrf_token: str | None = None


class PasswordForgotRequest(BaseModel):
    """Validate a password recovery request."""

    email: EmailStr
    country_code: str = Field(min_length=2, max_length=2)
    locale_code: str = Field(min_length=2, max_length=32)


class PasswordResetRequest(BaseModel):
    """Validate a one-time password reset grant."""

    grant: str = Field(min_length=32, max_length=512)
    new_password: str = Field(min_length=12, max_length=128)
    locale_code: str = Field(min_length=2, max_length=32)


class StepUpRequest(BaseModel):
    """Validate a session-bound step-up request."""

    password: str = Field(min_length=1, max_length=128)
    action_class: str = Field(min_length=1, max_length=64)
    locale_code: str = Field(min_length=2, max_length=32)


class RevokeAllRequest(BaseModel):
    """Select whether the current session remains refreshable."""

    keep_current: bool = False


class SessionResponse(BaseModel):
    """Expose safe device and session metadata."""

    session_id: UUID
    device_id: str | None
    device_name: str | None
    issued_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    is_current: bool


class MessageResponse(BaseModel):
    """Return a generic successful operation message."""

    message: str
