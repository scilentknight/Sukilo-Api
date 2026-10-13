"""Cache resolved authorization projections safely."""

import json
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from threading import Lock
from typing import Protocol
from uuid import UUID

from app.modules.access.policies import AssignmentProjection, ResourceScope
from app.modules.access.types import ScopeType


MAX_CACHE_TTL_SECONDS = 300


class CacheBackend(Protocol):
    """Describe the authorization cache operations."""

    def get(self, key: str) -> bytes | str | None:
        """Return a cached value."""
        ...

    def setex(self, key: str, ttl: int, value: str) -> object:
        """Store an expiring cached value."""
        ...

    def delete(self, key: str) -> object:
        """Delete a cached value."""
        ...


class InMemoryCacheBackend:
    """Store expiring authorization values for single-process development."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        """Initialize an empty expiring cache."""
        self.clock = clock
        self.values: dict[str, tuple[datetime, str]] = {}
        self.lock = Lock()

    def get(self, key: str) -> str | None:
        """Return an unexpired cached value."""
        with self.lock:
            record = self.values.get(key)
            if record is None:
                return None
            expires_at, value = record
            if expires_at <= self.clock():
                self.values.pop(key, None)
                return None
            return value

    def setex(self, key: str, ttl: int, value: str) -> None:
        """Store a value until its relative expiry."""
        with self.lock:
            self.values[key] = (
                self.clock() + timedelta(seconds=ttl),
                value,
            )

    def delete(self, key: str) -> None:
        """Delete one cached value."""
        with self.lock:
            self.values.pop(key, None)


class AuthorizationCache:
    """Cache version-bound authorization projections."""

    def __init__(
        self,
        *,
        backend: CacheBackend,
        clock: Callable[[], datetime],
    ) -> None:
        """Initialize cache dependencies."""
        self.backend = backend
        self.clock = clock

    def get(
        self,
        user_id: UUID,
        auth_version: int,
    ) -> list[AssignmentProjection] | None:
        """Return safe cached projections or a cache miss."""
        try:
            value = self.backend.get(_cache_key(user_id, auth_version))
            if value is None:
                return None
            payload = json.loads(value)
            projections = [_deserialize(item) for item in payload]
            if not projections and payload != []:
                return None
            now = self.clock()
            if any(not _is_active(item, now) for item in projections):
                return None
            return projections
        except (Exception,):
            return None

    def put(
        self,
        user_id: UUID,
        auth_version: int,
        projections: Iterable[AssignmentProjection],
    ) -> None:
        """Cache projections within their validity bounds."""
        items = list(projections)
        ttl = _ttl_seconds(items, self.clock())
        if ttl <= 0:
            return
        payload = json.dumps([_serialize(item) for item in items])
        try:
            self.backend.setex(
                _cache_key(user_id, auth_version),
                ttl,
                payload,
            )
        except (Exception,):
            return

    def invalidate(self, user_id: UUID, auth_version: int) -> None:
        """Delete one versioned authorization key."""
        try:
            self.backend.delete(_cache_key(user_id, auth_version))
        except (Exception,):
            return


def _cache_key(user_id: UUID, auth_version: int) -> str:
    return f"iamz:{user_id}:{auth_version}"


def _ttl_seconds(
    projections: list[AssignmentProjection],
    now: datetime,
) -> int:
    boundaries = [
        int((item.valid_to - now).total_seconds())
        for item in projections
        if item.valid_to is not None
    ]
    return min([MAX_CACHE_TTL_SECONDS, *boundaries])


def _serialize(projection: AssignmentProjection) -> dict[str, object]:
    return {
        "assignment_id": str(projection.assignment_id),
        "scope_type": projection.scope.scope_type.value,
        "scope_id": _uuid_string(projection.scope.scope_id),
        "country_id": _uuid_string(projection.scope.country_id),
        "vendor_id": _uuid_string(projection.scope.vendor_id),
        "permissions": sorted(projection.permissions),
        "valid_from": _datetime_string(projection.valid_from),
        "valid_to": _datetime_string(projection.valid_to),
        "revoked_at": _datetime_string(projection.revoked_at),
    }


def _deserialize(value: object) -> AssignmentProjection:
    if not isinstance(value, dict):
        raise ValueError("Cached authorization projection is invalid.")
    scope = ResourceScope(
        scope_type=ScopeType(value["scope_type"]),
        scope_id=_optional_uuid(value["scope_id"]),
        country_id=_optional_uuid(value["country_id"]),
        vendor_id=_optional_uuid(value["vendor_id"]),
    )
    permissions = value["permissions"]
    if not isinstance(permissions, list):
        raise ValueError("Cached permission projection is invalid.")
    return AssignmentProjection(
        assignment_id=UUID(str(value["assignment_id"])),
        scope=scope,
        permissions=frozenset(str(item) for item in permissions),
        valid_from=_optional_datetime(value["valid_from"]),
        valid_to=_optional_datetime(value["valid_to"]),
        revoked_at=_optional_datetime(value["revoked_at"]),
    )


def _is_active(projection: AssignmentProjection, now: datetime) -> bool:
    return (
        projection.revoked_at is None
        and (
            projection.valid_from is None
            or projection.valid_from <= now
        )
        and (projection.valid_to is None or projection.valid_to > now)
    )


def _uuid_string(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _datetime_string(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else UUID(str(value))


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else datetime.fromisoformat(str(value))
