"""Test trusted access-control resource scope validation."""

from uuid import UUID

import pytest

from app.modules.access.policies import ResourceScope, ScopeValidationError
from app.modules.access.types import ScopeType


COUNTRY_ID = UUID(int=1)
VENDOR_ID = UUID(int=2)
STORE_ID = UUID(int=3)


def test_global_scope_rejects_resource_identifiers() -> None:
    """Reject identifiers that conflict with a global scope."""
    with pytest.raises(ScopeValidationError):
        ResourceScope(
            scope_type=ScopeType.GLOBAL,
            scope_id=COUNTRY_ID,
            country_id=None,
            vendor_id=None,
        )


def test_country_scope_requires_matching_country_id() -> None:
    """Require the canonical scope ID to equal the country ID."""
    with pytest.raises(ScopeValidationError):
        ResourceScope(
            scope_type=ScopeType.COUNTRY,
            scope_id=UUID(int=9),
            country_id=COUNTRY_ID,
            vendor_id=None,
        )
    scope = ResourceScope(
        scope_type=ScopeType.COUNTRY,
        scope_id=COUNTRY_ID,
        country_id=COUNTRY_ID,
        vendor_id=None,
    )
    assert scope.country_id == COUNTRY_ID


def test_vendor_and_store_scopes_require_ancestry() -> None:
    """Require country and vendor ancestry for narrow resource scopes."""
    with pytest.raises(ScopeValidationError):
        ResourceScope(
            scope_type=ScopeType.VENDOR,
            scope_id=VENDOR_ID,
            country_id=None,
            vendor_id=VENDOR_ID,
        )
    vendor = ResourceScope(
        scope_type=ScopeType.VENDOR,
        scope_id=VENDOR_ID,
        country_id=COUNTRY_ID,
        vendor_id=VENDOR_ID,
    )
    store = ResourceScope(
        scope_type=ScopeType.STORE,
        scope_id=STORE_ID,
        country_id=COUNTRY_ID,
        vendor_id=VENDOR_ID,
    )
    assert vendor.vendor_id == VENDOR_ID
    assert store.scope_id == STORE_ID
