"""Enforce customer preference and consent domain rules."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.auth_context import AuthContext
from app.core.database_context import (
    ADDRESS_SOFT_DELETE_ACTION,
    CUSTOMER_ADDRESS_ACTION,
    CUSTOMER_CONSENT_ACTION,
    AuthorizationMode,
    DatabaseRequestContext,
    bind_database_context,
)
from app.core.identifiers import IdentifierValidationError, normalize_phone_e164
from app.core.transactions import DatabaseTransaction
from app.modules.customers.models import Address, ConsentRecord, UserPreference
from app.modules.customers.ports import (
    AddressRepositoryPort,
    Clock,
    ConsentRepositoryPort,
    PreferenceRepositoryPort,
)
from app.modules.customers.types import (
    AddressType,
    ConsentSource,
    ConsentType,
    DefaultKind,
)
from app.ports.audit import AuditEventInput, AuditWriter
from app.ports.country import AddressPolicy, CountryReader, CountryReference


class CustomerServiceError(Exception):
    """Report a safe customer domain failure."""


class PreferenceValidationError(CustomerServiceError):
    """Report invalid preference input or country configuration."""


class PreferenceConflictError(CustomerServiceError):
    """Report a stale preference version."""


class ConsentValidationError(CustomerServiceError):
    """Report invalid customer consent evidence."""


class AddressValidationError(CustomerServiceError):
    """Report invalid customer address input."""


class AddressNotFoundError(CustomerServiceError):
    """Hide missing, cross-owner, and cross-country addresses."""


class AddressConflictError(CustomerServiceError):
    """Report a stale customer address version."""


@dataclass(frozen=True, slots=True)
class CreateAddress:
    """Describe one customer-owned address creation."""

    actor: AuthContext
    country_code: str
    geography_area_id: UUID | None
    recipient_name: str
    phone: str
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


@dataclass(frozen=True, slots=True)
class UpdateAddress(CreateAddress):
    """Describe one optimistic full address replacement."""

    address_id: UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class AddressView:
    """Expose one active customer address."""

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


@dataclass(frozen=True, slots=True)
class SetPreferences:
    """Describe a complete authenticated preference replacement."""

    actor: AuthContext
    country_code: str
    locale_code: str
    currency_code: str
    expected_version: int | None


@dataclass(frozen=True, slots=True)
class PreferenceView:
    """Expose safe account preference state."""

    user_id: UUID
    country_id: UUID | None
    country_code: str | None
    locale_code: str | None
    currency_code: str | None
    marketing_email: bool
    marketing_sms: bool
    version: int


@dataclass(frozen=True, slots=True)
class RecordConsent:
    """Describe one explicit customer consent action."""

    actor: AuthContext
    country_code: str
    consent_type: ConsentType
    policy_version: str
    granted: bool
    source: ConsentSource


@dataclass(frozen=True, slots=True)
class ConsentHistoryPage:
    """Expose one bounded consent history page."""

    items: list["ConsentView"]
    next_cursor: tuple[datetime, UUID] | None


@dataclass(frozen=True, slots=True)
class ConsentView:
    """Expose safe consent evidence."""

    consent_id: UUID
    user_id: UUID
    country_id: UUID
    consent_type: ConsentType
    policy_version: str
    granted: bool
    source: ConsentSource
    recorded_at: datetime
    correlation_id: UUID


class AddressService:
    """Manage country-scoped customer-owned addresses."""

    def __init__(
        self,
        *,
        repository: AddressRepositoryPort,
        countries: CountryReader,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Clock,
    ) -> None:
        """Initialize address dependencies."""
        self.repository = repository
        self.countries = countries
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock

    def list_addresses(
        self,
        actor: AuthContext,
        country_code: str,
    ) -> list[AddressView]:
        """Return active addresses for the authenticated owner and country."""
        country = self._resolve_address_country(country_code)
        self._bind(actor, country.country_id, CUSTOMER_ADDRESS_ACTION)
        rows = self.repository.list_addresses(actor.user_id, country.country_id)
        return [_address_view(row) for row in rows]

    def create_address(self, command: CreateAddress) -> AddressView:
        """Create, normalize, default, and audit one address."""
        country = self._resolve_address_country(command.country_code)
        self._bind(command.actor, country.country_id, CUSTOMER_ADDRESS_ACTION)
        phone = self._validate(command, country)
        kinds = _eligible_kinds(command)
        rows = self._lock_scope(command.actor.user_id, country.country_id, kinds)
        now = self.clock()
        row = _new_address(command, country.country_id, phone, now)
        for kind in kinds:
            requested = _command_default(command, kind)
            if requested:
                _clear_default(rows, kind)
            if requested or not _has_default(rows, kind):
                _set_default_flag(row, kind, True)
        self.repository.add_address(row)
        self.repository.flush()
        self._stage(command.actor, row, "created", _created_address_fields(row))
        return _address_view(row)

    def get_address(
        self,
        actor: AuthContext,
        country_code: str,
        address_id: UUID,
    ) -> AddressView:
        """Return one active exactly scoped address or hidden not found."""
        country = self._resolve_address_country(country_code)
        self._bind(actor, country.country_id, CUSTOMER_ADDRESS_ACTION)
        row = self._get(actor.user_id, country.country_id, address_id)
        return _address_view(row)

    def update_address(self, command: UpdateAddress) -> AddressView:
        """Replace one address with optimistic concurrency and safe defaults."""
        country = self._resolve_address_country(command.country_code)
        self._bind(command.actor, country.country_id, CUSTOMER_ADDRESS_ACTION)
        rows = self._lock_scope(
            command.actor.user_id,
            country.country_id,
            (DefaultKind.SHIPPING, DefaultKind.BILLING),
        )
        row = _find_address(rows, command.address_id)
        phone = self._validate(command, country)
        if row.version != command.expected_version:
            raise AddressConflictError("Address version is stale.")
        changed = _changed_address_fields(row, command, phone)
        old_defaults = {
            DefaultKind.SHIPPING: row.is_default_shipping,
            DefaultKind.BILLING: row.is_default_billing,
        }
        _replace_address(row, command, phone, self.clock())
        for kind in (DefaultKind.SHIPPING, DefaultKind.BILLING):
            self._reconcile_updated_default(row, rows, command, kind, old_defaults[kind])
        row.version += 1
        self.repository.flush()
        self._stage(command.actor, row, "updated", changed)
        return _address_view(row)

    def delete_address(
        self,
        actor: AuthContext,
        country_code: str,
        address_id: UUID,
        expected_version: int,
    ) -> None:
        """Soft-delete one address and promote eligible defaults."""
        country = self._resolve_address_country(country_code)
        self._bind(actor, country.country_id, ADDRESS_SOFT_DELETE_ACTION)
        rows = self._lock_scope(
            actor.user_id,
            country.country_id,
            (DefaultKind.SHIPPING, DefaultKind.BILLING),
        )
        row = _find_address(rows, address_id)
        if row.version != expected_version:
            raise AddressConflictError("Address version is stale.")
        old_defaults = (row.is_default_shipping, row.is_default_billing)
        row.is_default_shipping = False
        row.is_default_billing = False
        row.deleted_at = self.clock()
        row.updated_at = row.deleted_at
        row.version += 1
        self.repository.flush()
        for kind, was_default in zip(
            (DefaultKind.SHIPPING, DefaultKind.BILLING),
            old_defaults,
            strict=True,
        ):
            if was_default:
                _promote_default(rows, kind, row)
        self.repository.flush()
        self._stage(actor, row, "deleted", ("deleted_at",))

    def set_default(
        self,
        actor: AuthContext,
        country_code: str,
        address_id: UUID,
        kind: DefaultKind,
    ) -> AddressView:
        """Select one eligible scoped address as a default."""
        country = self._resolve_address_country(country_code)
        self._bind(actor, country.country_id, CUSTOMER_ADDRESS_ACTION)
        if not isinstance(kind, DefaultKind):
            raise AddressValidationError("Default kind is invalid.")
        self.repository.lock_default_scope(actor.user_id, country.country_id, kind)
        rows = self.repository.lock_active_addresses(actor.user_id, country.country_id)
        row = next((item for item in rows if item.address_id == address_id), None)
        if row is None:
            raise AddressNotFoundError("Address is unavailable.")
        if not _is_eligible(row, kind):
            raise AddressValidationError("Address is ineligible for this default.")
        if _clear_default(rows, kind, exclude=row):
            self.repository.flush()
        if not _default_flag(row, kind):
            _set_default_flag(row, kind, True)
            _touch_version(row, self.clock())
        self.repository.flush()
        self._stage(actor, row, "default_changed", (_default_field(kind),))
        return _address_view(row)

    def _bind(
        self,
        actor: AuthContext,
        country_id: UUID,
        action_class: str,
    ) -> None:
        """Set self-service database context for one address transaction."""
        bind_database_context(
            self.transaction.session,
            DatabaseRequestContext(
                authorization_mode=AuthorizationMode.SELF,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                effective_country_id=country_id,
                action_class=action_class,
                correlation_id=actor.correlation_id,
            ),
        )

    def _resolve_address_country(self, country_code: str) -> CountryReference:
        country = _resolve_country(
            self.countries,
            country_code,
            AddressValidationError,
        )
        if not self.countries.is_operation_enabled(
            country.country_id,
            "CUSTOMER_ADDRESS",
        ):
            raise AddressValidationError("Country addresses are unavailable.")
        return country

    def _validate(
        self,
        command: CreateAddress,
        country: CountryReference,
    ) -> str:
        policy = self.countries.get_address_policy(country.country_id)
        _validate_address_values(command, policy)
        if command.geography_area_id is not None and not (
            self.countries.geography_belongs_to_country(
                country.country_id,
                command.geography_area_id,
            )
        ):
            raise AddressValidationError("Address geography is unavailable.")
        try:
            return normalize_phone_e164(command.phone, country.code)
        except IdentifierValidationError as error:
            raise AddressValidationError("Delivery phone is invalid.") from error

    def _get(
        self,
        user_id: UUID,
        country_id: UUID,
        address_id: UUID,
        *,
        for_update: bool = False,
    ) -> Address:
        row = self.repository.get_address(
            user_id,
            country_id,
            address_id,
            for_update=for_update,
        )
        if row is None:
            raise AddressNotFoundError("Address is unavailable.")
        return row

    def _lock_scope(
        self,
        user_id: UUID,
        country_id: UUID,
        kinds: tuple[DefaultKind, ...],
    ) -> list[Address]:
        for kind in kinds:
            self.repository.lock_default_scope(user_id, country_id, kind)
        return self.repository.lock_active_addresses(user_id, country_id)

    def _reconcile_updated_default(
        self,
        row: Address,
        rows: list[Address],
        command: UpdateAddress,
        kind: DefaultKind,
        was_default: bool,
    ) -> None:
        requested = _command_default(command, kind)
        if requested:
            if _clear_default(rows, kind, exclude=row):
                self.repository.flush()
            _set_default_flag(row, kind, True)
            return
        _set_default_flag(row, kind, False)
        if was_default:
            self.repository.flush()
            _promote_default(rows, kind, row)
        elif _is_eligible(row, kind) and not _has_default(rows, kind):
            _set_default_flag(row, kind, True)

    def _stage(
        self,
        actor: AuthContext,
        row: Address,
        action: str,
        changed_fields: tuple[str, ...],
    ) -> None:
        event = AuditEventInput(
            action=f"iam.customer.address.{action}",
            resource_type="iam.address",
            resource_id=row.address_id,
            correlation_id=actor.correlation_id,
            actor_user_id=actor.user_id,
            country_id=row.country_id,
            after_data={"changed_fields": changed_fields},
        )
        self.audit_writer.stage(event, self.transaction)


class PreferenceService:
    """Manage one non-authoritative preference row per account."""

    def __init__(
        self,
        *,
        repository: PreferenceRepositoryPort,
        countries: CountryReader,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Clock,
    ) -> None:
        """Initialize preference dependencies."""
        self.repository = repository
        self.countries = countries
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock

    def get_preferences(self, actor: AuthContext) -> PreferenceView | None:
        """Return the authenticated account's preference row."""
        row = self.repository.get(actor.user_id)
        if row is None:
            return None
        country_code = _safe_country_code(self.countries, row.preferred_country_id)
        return _preference_view(row, country_code)

    def set_preferences(self, command: SetPreferences) -> PreferenceView:
        """Create or optimistically replace complete market preferences."""
        country = self._validate_combination(command)
        row = self.repository.get(command.actor.user_id, for_update=True)
        now = self.clock()
        if row is None:
            if command.expected_version is not None:
                raise PreferenceConflictError("Preference version is stale.")
            row = _new_preference(command, country.country_id, now)
            changed_fields = _preference_field_names()
            self.repository.add(row)
        else:
            if command.expected_version != row.version:
                raise PreferenceConflictError("Preference version is stale.")
            changed_fields = _changed_preference_fields(row, command, country)
            _replace_preference(row, command, country.country_id, now)
        self.repository.flush()
        self._stage_change(command, row, country.country_id, changed_fields)
        return _preference_view(row, country.code)

    def _validate_combination(
        self,
        command: SetPreferences,
    ) -> CountryReference:
        if not command.country_code or not command.locale_code or not command.currency_code:
            raise PreferenceValidationError("Complete preferences are required.")
        country = _resolve_country(self.countries, command.country_code, PreferenceValidationError)
        if not self.countries.is_operation_enabled(country.country_id, "CUSTOMER_PREFERENCE"):
            raise PreferenceValidationError("Country preferences are unavailable.")
        if not self.countries.is_locale_enabled(country.country_id, command.locale_code):
            raise PreferenceValidationError("Preference locale is unavailable.")
        if not self.countries.is_currency_enabled(country.country_id, command.currency_code):
            raise PreferenceValidationError("Preference currency is unavailable.")
        return country

    def _stage_change(
        self,
        command: SetPreferences,
        row: UserPreference,
        country_id: UUID,
        changed_fields: tuple[str, ...],
    ) -> None:
        event = AuditEventInput(
            action="iam.customer.preference.changed",
            resource_type="iam.user_preference",
            resource_id=row.user_id,
            correlation_id=command.actor.correlation_id,
            actor_user_id=command.actor.user_id,
            country_id=country_id,
            after_data={"changed_fields": changed_fields},
        )
        self.audit_writer.stage(event, self.transaction)


class ConsentService:
    """Record immutable customer consent and marketing projections."""

    def __init__(
        self,
        *,
        repository: ConsentRepositoryPort,
        countries: CountryReader,
        audit_writer: AuditWriter,
        transaction: DatabaseTransaction,
        clock: Clock,
    ) -> None:
        """Initialize consent dependencies."""
        self.repository = repository
        self.countries = countries
        self.audit_writer = audit_writer
        self.transaction = transaction
        self.clock = clock

    def record(self, command: RecordConsent) -> ConsentView:
        """Append one server-timed consent record."""
        country = self._validate(command)
        self._bind(command.actor, country.country_id)
        now = self.clock()
        row = ConsentRecord(
            consent_id=uuid4(),
            user_id=command.actor.user_id,
            country_id=country.country_id,
            consent_type=command.consent_type,
            policy_version=command.policy_version,
            granted=command.granted,
            source=command.source,
            recorded_at=now,
            correlation_id=command.actor.correlation_id,
        )
        self.repository.add_consent(row)
        if command.consent_type in _marketing_types():
            self._update_marketing(command, country.country_id, now)
        self.repository.flush()
        self._stage_record(command, row)
        return _consent_view(row)

    def list_current(
        self,
        actor: AuthContext,
        country_code: str,
    ) -> list[ConsentView]:
        """Return deterministic current consent state for one country."""
        country = _resolve_country(self.countries, country_code, ConsentValidationError)
        self._bind(actor, country.country_id)
        rows = self.repository.list_current_consents(actor.user_id, country.country_id)
        return [_consent_view(row) for row in rows]

    def _bind(self, actor: AuthContext, country_id: UUID) -> None:
        """Set self-service database context for one consent transaction."""
        bind_database_context(
            self.transaction.session,
            DatabaseRequestContext(
                authorization_mode=AuthorizationMode.SELF,
                actor_user_id=actor.user_id,
                effective_user_id=actor.user_id,
                effective_country_id=country_id,
                action_class=CUSTOMER_CONSENT_ACTION,
                correlation_id=actor.correlation_id,
            ),
        )

    def list_history(
        self,
        actor: AuthContext,
        country_code: str,
        *,
        limit: int,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> ConsentHistoryPage:
        """Return an explicitly bounded consent evidence page."""
        if not 1 <= limit <= 100:
            raise ConsentValidationError("Consent history limit is invalid.")
        country = _resolve_country(self.countries, country_code, ConsentValidationError)
        self._bind(actor, country.country_id)
        rows = self.repository.list_consent_history(
            actor.user_id,
            country.country_id,
            cursor=cursor,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = (last.recorded_at, last.consent_id)
        return ConsentHistoryPage(
            items=[_consent_view(row) for row in page_rows],
            next_cursor=next_cursor,
        )

    def _validate(self, command: RecordConsent) -> CountryReference:
        if not command.country_code or not command.policy_version:
            raise ConsentValidationError("Country and policy version are required.")
        if command.source not in {ConsentSource.WEB, ConsentSource.APP}:
            raise ConsentValidationError("Consent source is unavailable.")
        country = _resolve_country(self.countries, command.country_code, ConsentValidationError)
        try:
            active_version = self.countries.get_active_policy_version(
                country.country_id,
                command.consent_type.value,
            )
        except (KeyError, ValueError) as error:
            raise ConsentValidationError("Consent policy is unavailable.") from error
        if command.policy_version != active_version:
            raise ConsentValidationError("Consent policy version is stale.")
        return country

    def _update_marketing(
        self,
        command: RecordConsent,
        country_id: UUID,
        now: datetime,
    ) -> None:
        preference = self.repository.get_preference(
            command.actor.user_id,
            for_update=True,
        )
        if preference is None:
            preference = UserPreference(
                user_id=command.actor.user_id,
                preferred_country_id=None,
                preferred_locale_code=None,
                preferred_currency_code=None,
                marketing_email=False,
                marketing_sms=False,
                created_at=now,
                updated_at=now,
                version=1,
            )
            self.repository.add_preference(preference)
        else:
            preference.version += 1
            preference.updated_at = now
        if command.consent_type == ConsentType.MARKETING_EMAIL:
            preference.marketing_email = command.granted
        else:
            preference.marketing_sms = command.granted

    def _stage_record(
        self,
        command: RecordConsent,
        row: ConsentRecord,
    ) -> None:
        event = AuditEventInput(
            action="iam.customer.consent.recorded",
            resource_type="iam.consent_record",
            resource_id=row.consent_id,
            correlation_id=command.actor.correlation_id,
            actor_user_id=command.actor.user_id,
            country_id=row.country_id,
            after_data={
                "changed_fields": ("granted",),
                "consent_type": command.consent_type.value,
            },
        )
        self.audit_writer.stage(event, self.transaction)


def _validate_address_values(
    command: CreateAddress,
    policy: AddressPolicy,
) -> None:
    if not isinstance(command.address_type, AddressType):
        raise AddressValidationError("Address type is invalid.")
    if not command.is_shipping and not command.is_billing:
        raise AddressValidationError("Address must support shipping or billing.")
    if command.is_default_shipping and not command.is_shipping:
        raise AddressValidationError("Shipping default must support shipping.")
    if command.is_default_billing and not command.is_billing:
        raise AddressValidationError("Billing default must support billing.")
    _validate_address_lengths(command)
    if policy.postal_code_required and not _present(command.postal_code):
        raise AddressValidationError("Postal code is required.")
    if policy.geography_required and command.geography_area_id is None:
        raise AddressValidationError("Address geography is required.")
    coordinates_present = command.latitude is not None or command.longitude is not None
    if coordinates_present and not policy.coordinates_allowed:
        raise AddressValidationError("Address coordinates are unavailable.")
    if (command.latitude is None) != (command.longitude is None):
        raise AddressValidationError("Address coordinates must be paired.")
    if command.latitude is not None and not Decimal("-90") <= command.latitude <= Decimal("90"):
        raise AddressValidationError("Address latitude is invalid.")
    if command.longitude is not None and not Decimal("-180") <= command.longitude <= Decimal("180"):
        raise AddressValidationError("Address longitude is invalid.")


def _validate_address_lengths(command: CreateAddress) -> None:
    values = (
        ("recipient_name", command.recipient_name, 160, True),
        ("phone", command.phone, 64, True),
        ("address_line_1", command.address_line_1, 255, True),
        ("address_line_2", command.address_line_2, 255, False),
        ("landmark", command.landmark, 255, False),
        ("city_text", command.city_text, 120, False),
        ("postal_code", command.postal_code, 32, False),
    )
    for field, value, maximum, required in values:
        if required and not _present(value):
            raise AddressValidationError(f"{field} is required.")
        if value is not None and len(value.strip()) > maximum:
            raise AddressValidationError(f"{field} is too long.")


def _present(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _new_address(
    command: CreateAddress,
    country_id: UUID,
    phone: str,
    now: datetime,
) -> Address:
    return Address(
        address_id=uuid4(),
        user_id=command.actor.user_id,
        country_id=country_id,
        geography_area_id=command.geography_area_id,
        recipient_name=command.recipient_name.strip(),
        phone_e164=phone,
        address_line_1=command.address_line_1.strip(),
        address_line_2=_strip_optional(command.address_line_2),
        landmark=_strip_optional(command.landmark),
        city_text=_strip_optional(command.city_text),
        postal_code=_strip_optional(command.postal_code),
        latitude=command.latitude,
        longitude=command.longitude,
        address_type=command.address_type,
        is_shipping=command.is_shipping,
        is_billing=command.is_billing,
        is_default_shipping=False,
        is_default_billing=False,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        version=1,
    )


def _replace_address(
    row: Address,
    command: UpdateAddress,
    phone: str,
    now: datetime,
) -> None:
    row.geography_area_id = command.geography_area_id
    row.recipient_name = command.recipient_name.strip()
    row.phone_e164 = phone
    row.address_line_1 = command.address_line_1.strip()
    row.address_line_2 = _strip_optional(command.address_line_2)
    row.landmark = _strip_optional(command.landmark)
    row.city_text = _strip_optional(command.city_text)
    row.postal_code = _strip_optional(command.postal_code)
    row.latitude = command.latitude
    row.longitude = command.longitude
    row.address_type = command.address_type
    row.is_shipping = command.is_shipping
    row.is_billing = command.is_billing
    row.updated_at = now


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _eligible_kinds(command: CreateAddress) -> tuple[DefaultKind, ...]:
    kinds = []
    if command.is_shipping:
        kinds.append(DefaultKind.SHIPPING)
    if command.is_billing:
        kinds.append(DefaultKind.BILLING)
    return tuple(kinds)


def _command_default(command: CreateAddress, kind: DefaultKind) -> bool:
    if kind == DefaultKind.SHIPPING:
        return command.is_default_shipping
    return command.is_default_billing


def _default_field(kind: DefaultKind) -> str:
    if kind == DefaultKind.SHIPPING:
        return "is_default_shipping"
    return "is_default_billing"


def _eligibility_field(kind: DefaultKind) -> str:
    if kind == DefaultKind.SHIPPING:
        return "is_shipping"
    return "is_billing"


def _default_flag(row: Address, kind: DefaultKind) -> bool:
    return bool(getattr(row, _default_field(kind)))


def _set_default_flag(row: Address, kind: DefaultKind, value: bool) -> None:
    setattr(row, _default_field(kind), value)


def _is_eligible(row: Address, kind: DefaultKind) -> bool:
    return bool(getattr(row, _eligibility_field(kind))) and row.deleted_at is None


def _has_default(rows: list[Address], kind: DefaultKind) -> bool:
    return any(_default_flag(row, kind) for row in rows if row.deleted_at is None)


def _find_address(rows: list[Address], address_id: UUID) -> Address:
    row = next((item for item in rows if item.address_id == address_id), None)
    if row is None:
        raise AddressNotFoundError("Address is unavailable.")
    return row


def _clear_default(
    rows: list[Address],
    kind: DefaultKind,
    *,
    exclude: Address | None = None,
) -> bool:
    changed = False
    for row in rows:
        if row is not exclude and _default_flag(row, kind):
            _set_default_flag(row, kind, False)
            row.version += 1
            changed = True
    return changed


def _promote_default(
    rows: list[Address],
    kind: DefaultKind,
    excluded: Address,
) -> None:
    candidate = next(
        (
            row
            for row in rows
            if row is not excluded and _is_eligible(row, kind)
        ),
        None,
    )
    if candidate is not None:
        _set_default_flag(candidate, kind, True)
        candidate.version += 1


def _touch_version(row: Address, now: datetime) -> None:
    row.version += 1
    row.updated_at = now


def _created_address_fields(row: Address) -> tuple[str, ...]:
    candidates = (
        "geography_area_id",
        "recipient_name",
        "phone_e164",
        "address_line_1",
        "address_line_2",
        "landmark",
        "city_text",
        "postal_code",
        "latitude",
        "longitude",
        "address_type",
        "is_shipping",
        "is_billing",
        "is_default_shipping",
        "is_default_billing",
    )
    return tuple(
        field
        for field in candidates
        if getattr(row, field) not in (None, False)
    )


def _changed_address_fields(
    row: Address,
    command: UpdateAddress,
    phone: str,
) -> tuple[str, ...]:
    replacements = {
        "geography_area_id": command.geography_area_id,
        "recipient_name": command.recipient_name.strip(),
        "phone_e164": phone,
        "address_line_1": command.address_line_1.strip(),
        "address_line_2": _strip_optional(command.address_line_2),
        "landmark": _strip_optional(command.landmark),
        "city_text": _strip_optional(command.city_text),
        "postal_code": _strip_optional(command.postal_code),
        "latitude": command.latitude,
        "longitude": command.longitude,
        "address_type": command.address_type,
        "is_shipping": command.is_shipping,
        "is_billing": command.is_billing,
        "is_default_shipping": command.is_default_shipping,
        "is_default_billing": command.is_default_billing,
    }
    return tuple(
        field
        for field, value in replacements.items()
        if getattr(row, field) != value
    )


def _address_view(row: Address) -> AddressView:
    return AddressView(
        address_id=row.address_id,
        user_id=row.user_id,
        country_id=row.country_id,
        geography_area_id=row.geography_area_id,
        recipient_name=row.recipient_name,
        phone_e164=row.phone_e164,
        address_line_1=row.address_line_1,
        address_line_2=row.address_line_2,
        landmark=row.landmark,
        city_text=row.city_text,
        postal_code=row.postal_code,
        latitude=row.latitude,
        longitude=row.longitude,
        address_type=AddressType(row.address_type),
        is_shipping=row.is_shipping,
        is_billing=row.is_billing,
        is_default_shipping=row.is_default_shipping,
        is_default_billing=row.is_default_billing,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


def _safe_country_code(
    countries: CountryReader,
    country_id: UUID | None,
) -> str | None:
    if country_id is None:
        return None
    try:
        return countries.resolve_country_id(country_id).code
    except (KeyError, ValueError, AttributeError):
        return None


def _resolve_country(
    countries: CountryReader,
    code: str,
    error_type: type[CustomerServiceError],
) -> CountryReference:
    if not code:
        raise error_type("Country is required.")
    try:
        return countries.resolve_country(code)
    except (KeyError, ValueError) as error:
        raise error_type("Country is unavailable.") from error


def _new_preference(
    command: SetPreferences,
    country_id: UUID,
    now: datetime,
) -> UserPreference:
    return UserPreference(
        user_id=command.actor.user_id,
        preferred_country_id=country_id,
        preferred_locale_code=command.locale_code,
        preferred_currency_code=command.currency_code,
        marketing_email=False,
        marketing_sms=False,
        created_at=now,
        updated_at=now,
        version=1,
    )


def _replace_preference(
    row: UserPreference,
    command: SetPreferences,
    country_id: UUID,
    now: datetime,
) -> None:
    row.preferred_country_id = country_id
    row.preferred_locale_code = command.locale_code
    row.preferred_currency_code = command.currency_code
    row.updated_at = now
    row.version += 1


def _changed_preference_fields(
    row: UserPreference,
    command: SetPreferences,
    country: CountryReference,
) -> tuple[str, ...]:
    changed = []
    if row.preferred_country_id != country.country_id:
        changed.append("preferred_country_id")
    if row.preferred_locale_code != command.locale_code:
        changed.append("preferred_locale_code")
    if row.preferred_currency_code != command.currency_code:
        changed.append("preferred_currency_code")
    return tuple(changed)


def _preference_field_names() -> tuple[str, ...]:
    return (
        "preferred_country_id",
        "preferred_locale_code",
        "preferred_currency_code",
    )


def _preference_view(
    row: UserPreference,
    country_code: str | None,
) -> PreferenceView:
    return PreferenceView(
        user_id=row.user_id,
        country_id=row.preferred_country_id,
        country_code=country_code,
        locale_code=row.preferred_locale_code,
        currency_code=row.preferred_currency_code,
        marketing_email=row.marketing_email,
        marketing_sms=row.marketing_sms,
        version=row.version,
    )


def _consent_view(row: ConsentRecord) -> ConsentView:
    return ConsentView(
        consent_id=row.consent_id,
        user_id=row.user_id,
        country_id=row.country_id,
        consent_type=ConsentType(row.consent_type),
        policy_version=row.policy_version,
        granted=row.granted,
        source=ConsentSource(row.source),
        recorded_at=row.recorded_at,
        correlation_id=row.correlation_id,
    )


def _marketing_types() -> frozenset[ConsentType]:
    return frozenset(
        {
            ConsentType.MARKETING_EMAIL,
            ConsentType.MARKETING_SMS,
        }
    )
