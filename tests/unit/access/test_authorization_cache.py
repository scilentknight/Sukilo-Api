"""Test fail-closed authorization projection caching."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.modules.access.application import AuthorizationProjectionService
from app.modules.access.cache import AuthorizationCache
from app.modules.access.policies import AssignmentProjection, ResourceScope
from app.modules.access.types import ScopeType


NOW = datetime(2026, 10, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
COUNTRY_ID = UUID(int=2)


class CacheBackendFake:
    """Store versioned cache values and optional failures."""

    def __init__(self) -> None:
        """Initialize backend state."""
        self.values = {}
        self.set_calls = []
        self.fail_get = False
        self.fail_set = False

    def get(self, key: str):
        """Return one cached value."""
        if self.fail_get:
            raise ConnectionError("cache unavailable")
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        """Store one expiring value."""
        if self.fail_set:
            raise ConnectionError("cache unavailable")
        self.values[key] = value
        self.set_calls.append((key, ttl, value))

    def delete(self, key: str) -> None:
        """Delete one cache key."""
        self.values.pop(key, None)


def _projection(valid_to: datetime | None) -> AssignmentProjection:
    return AssignmentProjection(
        assignment_id=UUID(int=3),
        scope=ResourceScope(
            ScopeType.COUNTRY,
            COUNTRY_ID,
            COUNTRY_ID,
            None,
        ),
        permissions=frozenset({"iam.assignment.read"}),
        valid_from=None,
        valid_to=valid_to,
        revoked_at=None,
    )


def test_cache_uses_versioned_key_and_nearest_validity_ttl() -> None:
    """Bound cached authority by auth version, five minutes, and validity."""
    backend = CacheBackendFake()
    cache = AuthorizationCache(backend=backend, clock=lambda: NOW)
    cache.put(USER_ID, 7, [_projection(NOW + timedelta(seconds=45))])
    key, ttl, _ = backend.set_calls[0]
    assert key == f"iamz:{USER_ID}:7"
    assert ttl == 45
    assert cache.get(USER_ID, 7) == [_projection(NOW + timedelta(seconds=45))]


def test_cache_caps_ttl_at_five_minutes() -> None:
    """Never retain authorization projections beyond five minutes."""
    backend = CacheBackendFake()
    cache = AuthorizationCache(backend=backend, clock=lambda: NOW)
    cache.put(USER_ID, 1, [_projection(None)])
    assert backend.set_calls[0][1] == 300


def test_cache_outage_and_invalid_payload_fall_back_to_database() -> None:
    """Return a miss instead of granting from unavailable or corrupt state."""
    backend = CacheBackendFake()
    cache = AuthorizationCache(backend=backend, clock=lambda: NOW)
    backend.fail_get = True
    assert cache.get(USER_ID, 1) is None
    backend.fail_get = False
    backend.values[f"iamz:{USER_ID}:1"] = json.dumps({"unexpected": True})
    assert cache.get(USER_ID, 1) is None


def test_cache_discards_expired_projection_even_before_key_expiry() -> None:
    """Never return authority whose validity boundary has passed."""
    backend = CacheBackendFake()
    cache = AuthorizationCache(backend=backend, clock=lambda: NOW)
    key = f"iamz:{USER_ID}:1"
    backend.values[key] = json.dumps(
        [
            {
                "assignment_id": str(UUID(int=3)),
                "scope_type": "COUNTRY",
                "scope_id": str(COUNTRY_ID),
                "country_id": str(COUNTRY_ID),
                "vendor_id": None,
                "permissions": ["iam.assignment.read"],
                "valid_from": None,
                "valid_to": (NOW - timedelta(seconds=1)).isoformat(),
                "revoked_at": None,
            }
        ]
    )
    assert cache.get(USER_ID, 1) is None


def test_cache_outage_falls_back_to_current_database_state() -> None:
    """Resolve current database authority whenever cache access fails."""
    backend = CacheBackendFake()
    backend.fail_get = True
    backend.fail_set = True
    projection = _projection(NOW + timedelta(minutes=1))
    repository = MagicRepository([projection])
    service = AuthorizationProjectionService(
        repository=repository,
        cache=AuthorizationCache(backend=backend, clock=lambda: NOW),
        clock=lambda: NOW,
    )
    assert service.resolve(USER_ID, 9) == [projection]
    assert repository.calls == [(USER_ID, NOW)]


class MagicRepository:
    """Return current database projections."""

    def __init__(self, projections: list[AssignmentProjection]) -> None:
        """Initialize current projections."""
        self.projections = projections
        self.calls = []

    def list_active_projections(
        self,
        user_id: UUID,
        now: datetime,
    ) -> list[AssignmentProjection]:
        """Return current projections."""
        self.calls.append((user_id, now))
        return self.projections
