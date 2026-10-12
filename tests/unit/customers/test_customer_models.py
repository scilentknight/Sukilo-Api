"""Test Phase 4 customer model metadata."""

from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric

from app.modules.customers import models
from app.modules.customers.types import AddressType, ConsentSource, ConsentType


def test_user_preference_matches_one_to_one_contract() -> None:
    """Require preference fields, account FK, and optimistic version."""
    table = models.UserPreference.__table__
    assert table.fullname == "iam.user_preference"
    assert {column.name for column in table.columns} == {
        "user_id",
        "preferred_country_id",
        "preferred_locale_code",
        "preferred_currency_code",
        "marketing_email",
        "marketing_sms",
        "created_at",
        "updated_at",
        "version",
    }
    user_fk = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if "user_account" in foreign_key.target_fullname
    )
    assert user_fk.parent.name == "user_id"
    assert user_fk.ondelete == "RESTRICT"
    assert table.c.version.server_default is not None
    assert table.c.marketing_email.server_default is not None
    assert table.c.marketing_sms.server_default is not None


def test_consent_record_matches_append_only_contract() -> None:
    """Require consent evidence fields, checks, and lookup indexes."""
    table = models.ConsentRecord.__table__
    assert table.fullname == "iam.consent_record"
    assert {column.name for column in table.columns} == {
        "consent_id",
        "user_id",
        "country_id",
        "consent_type",
        "policy_version",
        "granted",
        "source",
        "recorded_at",
        "correlation_id",
    }
    assert {
        "ix_consent_user_country_type_latest",
        "ix_consent_country_type_recorded",
    }.issubset({index.name for index in table.indexes})
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {"ck_consent_type", "ck_consent_source"}.issubset(check_names)
    user_fk = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if "user_account" in foreign_key.target_fullname
    )
    assert user_fk.ondelete == "RESTRICT"


def test_consent_enums_use_stable_str_values() -> None:
    """Keep consent type and source codes aligned with the spec."""
    assert [member.value for member in ConsentType] == [
        "TERMS",
        "PRIVACY",
        "MARKETING_EMAIL",
        "MARKETING_SMS",
        "ANALYTICS",
    ]
    assert [member.value for member in ConsentSource] == [
        "WEB",
        "APP",
        "ADMIN",
        "SYSTEM",
    ]


def test_address_matches_country_scoped_contract() -> None:
    """Require address fields, checks, active index, and default uniqueness."""
    table = models.Address.__table__
    assert table.fullname == "iam.address"
    assert {column.name for column in table.columns} == {
        "address_id",
        "user_id",
        "country_id",
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
        "created_at",
        "updated_at",
        "deleted_at",
        "version",
    }
    assert isinstance(table.c.latitude.type, Numeric)
    assert table.c.latitude.type.precision == 10
    assert table.c.latitude.type.scale == 7
    assert isinstance(table.c.longitude.type, Numeric)
    assert table.c.longitude.type.precision == 10
    assert table.c.longitude.type.scale == 7
    assert {
        "ix_address_user_country_active",
        "uq_address_default_shipping_user_country",
        "uq_address_default_billing_user_country",
    }.issubset({index.name for index in table.indexes})
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_address_type",
        "ck_address_shipping_or_billing",
        "ck_address_default_shipping_requires_shipping",
        "ck_address_default_billing_requires_billing",
        "ck_address_latitude_range",
        "ck_address_longitude_range",
        "ck_address_deleted_not_default",
    }.issubset(check_names)
    user_fk = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if "user_account" in foreign_key.target_fullname
    )
    assert user_fk.ondelete == "RESTRICT"


def test_address_type_enum_excludes_vendor_warehouse() -> None:
    """Accept only customer-facing address type codes."""
    assert [member.value for member in AddressType] == [
        "HOME",
        "OFFICE",
        "OTHER",
    ]


def test_address_coordinate_precision_round_trips() -> None:
    """Persist coordinates with seven decimal places."""
    table = models.Address.__table__
    latitude = table.c.latitude.type
    longitude = table.c.longitude.type
    sample = Decimal("27.7172453")
    assert latitude.python_type is Decimal
    assert longitude.python_type is Decimal
    assert latitude.asdecimal is True
    assert longitude.asdecimal is True
    assert str(sample) == "27.7172453"
