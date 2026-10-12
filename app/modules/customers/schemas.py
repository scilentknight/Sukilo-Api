"""Define public customer self-service API payloads."""

import base64
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.customers.types import AddressType, ConsentType, DefaultKind
from app.modules.identity.types import AccountStatus


class CurrentSessionResponse(BaseModel):
    """Expose the active session indicator only."""

    session_id: UUID
    device_id: str | None
    device_name: str | None
    issued_at: datetime
    is_current: bool


class PreferenceResponse(BaseModel):
    """Expose safe account preference state."""

    user_id: UUID
    country_id: UUID | None
    country_code: str | None
    locale_code: str | None
    currency_code: str | None
    marketing_email: bool
    marketing_sms: bool
    version: int


class ProfileResponse(BaseModel):
    """Expose the privacy-safe `/me` projection."""

    user_id: UUID
    status: AccountStatus
    email_masked: str | None
    phone_masked: str | None
    email_verified: bool
    phone_verified: bool
    created_at: datetime
    last_login_at: datetime | None
    preferences: PreferenceResponse | None
    current_session: CurrentSessionResponse


class UpdateMeRequest(BaseModel):
    """Start one verified identifier replacement."""

    model_config = ConfigDict(extra="forbid")
    new_email: EmailStr | None = None
    new_phone_e164: str | None = Field(default=None, max_length=24)
    country_code: str = Field(min_length=2, max_length=2)
    locale_code: str = Field(min_length=2, max_length=32)


class ConfirmIdentifierChangeRequest(BaseModel):
    """Confirm one identifier replacement with an opaque grant."""

    model_config = ConfigDict(extra="forbid")
    grant: str = Field(min_length=32, max_length=512)
    new_email: EmailStr | None = None
    new_phone_e164: str | None = Field(default=None, max_length=24)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    locale_code: str = Field(default="en", min_length=2, max_length=32)


class PreferenceUpdateRequest(BaseModel):
    """Replace complete market preferences with optimistic concurrency."""

    model_config = ConfigDict(extra="forbid")
    country_code: str = Field(min_length=2, max_length=2)
    locale_code: str = Field(min_length=2, max_length=12)
    currency_code: str = Field(min_length=3, max_length=3)
    expected_version: int = Field(ge=1)


class RecordConsentRequest(BaseModel):
    """Record one explicit consent action."""

    model_config = ConfigDict(extra="forbid")
    country_code: str = Field(min_length=2, max_length=2)
    consent_type: ConsentType
    policy_version: str = Field(min_length=1, max_length=32)
    granted: bool


class ConsentRecordResponse(BaseModel):
    """Expose safe consent evidence."""

    consent_id: UUID
    user_id: UUID
    country_id: UUID
    consent_type: ConsentType
    policy_version: str
    granted: bool
    source: str
    recorded_at: datetime
    correlation_id: UUID


class ConsentListResponse(BaseModel):
    """Return current or historical consent evidence."""

    items: list[ConsentRecordResponse]
    next_cursor: str | None = None


class AddressResponse(BaseModel):
    """Expose one customer-owned address."""

    address_id: UUID
    user_id: UUID
    country_id: UUID
    geography_area_id: UUID | None
    recipient_name: str
    phone_e164: str
    address_line_1: str
    address_line_2: str | None
    landmark: str | None
    city_text: str | None
    postal_code: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    address_type: AddressType
    is_shipping: bool
    is_billing: bool
    is_default_shipping: bool
    is_default_billing: bool
    created_at: datetime
    updated_at: datetime
    version: int


class AddressBodyRequest(BaseModel):
    """Validate address fields without country scope."""

    model_config = ConfigDict(extra="forbid")
    geography_area_id: UUID | None = None
    recipient_name: str = Field(min_length=1, max_length=160)
    phone: str = Field(min_length=3, max_length=64)
    address_line_1: str = Field(min_length=1, max_length=255)
    address_line_2: str | None = Field(default=None, max_length=255)
    landmark: str | None = Field(default=None, max_length=255)
    city_text: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=32)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    address_type: AddressType
    is_shipping: bool
    is_billing: bool
    is_default_shipping: bool = False
    is_default_billing: bool = False

    @field_validator("address_type")
    @classmethod
    def reject_vendor_address_type(cls, value: AddressType) -> AddressType:
        """Reject vendor-only address classifications."""
        if value not in {AddressType.HOME, AddressType.OFFICE, AddressType.OTHER}:
            raise ValueError("Address type is invalid.")
        return value


class AddressCreateRequest(AddressBodyRequest):
    """Validate one new country-scoped address."""

    country_code: str = Field(min_length=2, max_length=2)


class AddressUpdateRequest(AddressBodyRequest):
    """Replace one address with optimistic concurrency."""

    expected_version: int = Field(ge=1)


class SetDefaultAddressRequest(BaseModel):
    """Select one default address kind."""

    model_config = ConfigDict(extra="forbid")
    kind: DefaultKind


class IdentifierChangeChallengeResponse(BaseModel):
    """Return an opaque candidate verification challenge."""

    challenge_id: UUID


def encode_consent_cursor(recorded_at: datetime, consent_id: UUID) -> str:
    """Encode one opaque consent history cursor."""
    payload = {
        "recorded_at": recorded_at.isoformat(),
        "consent_id": str(consent_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_consent_cursor(value: str) -> tuple[datetime, UUID]:
    """Decode and validate one consent history cursor."""
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
        payload = json.loads(decoded.decode("utf-8"))
        recorded_at = datetime.fromisoformat(str(payload["recorded_at"]))
        consent_id = UUID(str(payload["consent_id"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Consent cursor is invalid.") from error
    if recorded_at.tzinfo is None:
        raise ValueError("Consent cursor is invalid.")
    return recorded_at, consent_id
