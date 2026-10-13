"""Test reusable in-memory IAM port adapters."""

import importlib.util
from types import ModuleType
from uuid import uuid4

import pytest

from app.core.transactions import DatabaseTransaction
from app.ports.audit import AuditEventInput
from app.ports.country import AddressPolicy, CountryReference


def _load_fakes() -> ModuleType:
    """Load IAM test fakes after asserting that they exist."""
    module_name = "tests.fakes.iam"
    assert importlib.util.find_spec(module_name) is not None
    return __import__(
        module_name,
        fromlist=["InMemoryCountryReader", "RecordingAuditWriter"],
    )


def test_country_reader_resolves_registered_country_policy() -> None:
    """Return only explicitly registered country capabilities."""
    fakes = _load_fakes()
    country_id = uuid4()
    geography_id = uuid4()
    policy = AddressPolicy(
        postal_code_required=True,
        geography_required=True,
        coordinates_allowed=False,
    )
    reader = fakes.InMemoryCountryReader()
    reader.add_country(
        CountryReference(country_id=country_id, code="NP", status="ACTIVE"),
        operations={"REGISTER"},
        locales={"en-NP"},
        currencies={"NPR"},
        geography_ids={geography_id},
        address_policy=policy,
        policy_versions={"PRIVACY": "2026-09"},
    )
    assert reader.resolve_country("NP").country_id == country_id
    assert reader.resolve_country_id(country_id).code == "NP"
    assert reader.is_operation_enabled(country_id, "REGISTER")
    assert reader.is_locale_enabled(country_id, "en-NP")
    assert reader.is_currency_enabled(country_id, "NPR")
    assert reader.geography_belongs_to_country(country_id, geography_id)
    assert reader.get_address_policy(country_id) == policy
    assert reader.get_active_policy_version(country_id, "PRIVACY") == "2026-09"
    assert not reader.is_operation_enabled(country_id, "CHECKOUT")


def test_recording_audit_writer_rejects_sensitive_payload_keys() -> None:
    """Reject credentials before recording audit evidence."""
    fakes = _load_fakes()
    writer = fakes.RecordingAuditWriter()
    transaction = DatabaseTransaction(session=object())
    event = AuditEventInput(
        action="iam.account.created",
        resource_type="user_account",
        resource_id=uuid4(),
        correlation_id=uuid4(),
        after_data={"password_hash": "secret"},
    )
    with pytest.raises(ValueError, match="sensitive"):
        writer.stage(event, transaction)
    assert writer.records == []


def test_recording_audit_writer_keeps_safe_event_and_transaction() -> None:
    """Record redacted evidence with its originating transaction."""
    fakes = _load_fakes()
    writer = fakes.RecordingAuditWriter()
    transaction = DatabaseTransaction(session=object())
    event = AuditEventInput(
        action="iam.account.created",
        resource_type="user_account",
        resource_id=uuid4(),
        correlation_id=uuid4(),
        after_data={"status": "PENDING"},
    )
    writer.stage(event, transaction)
    assert writer.records == [(event, transaction)]
