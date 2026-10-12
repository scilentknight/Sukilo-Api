"""Test single-use authorization grants."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.security import DigestService
from app.modules.identity.grants import AuthGrantError, AuthGrantService
from app.modules.identity.types import GrantPurpose


class GrantRepositoryFake:
    """Store grants by protected token digest."""

    def __init__(self) -> None:
        """Initialize empty storage."""
        self.rows = {}

    def add(self, grant):
        """Store a grant."""
        self.rows[grant.token_hash] = grant

    def get_by_token_hash_for_update(self, token_hash):
        """Return a grant by digest."""
        return self.rows.get(token_hash)

    def flush(self):
        """Accept staged changes."""


def test_grant_is_bound_and_consumed_once() -> None:
    """Prevent replay and cross-action grant use."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    repository = GrantRepositoryFake()
    service = AuthGrantService(
        repository=repository,
        digests=DigestService(active_key_id="v1", peppers={"v1": b"pepper"}),
        clock=lambda: now,
        token_source=lambda: "opaque-token",
    )
    user_id = UUID(int=1)
    raw_token = service.create(
        user_id=user_id,
        session_id=None,
        purpose=GrantPurpose.RESET_PASSWORD,
        action_class="password.reset",
        subject_hash=None,
        authentication_methods=["otp"],
        correlation_id=UUID(int=2),
        ttl=timedelta(minutes=10),
    )
    assert "opaque-token" not in next(iter(repository.rows))
    with pytest.raises(AuthGrantError, match="binding"):
        service.consume(
            raw_token,
            user_id=user_id,
            session_id=None,
            purpose=GrantPurpose.RESET_PASSWORD,
            action_class="email.change",
            subject_hash=None,
        )
    service.consume(
        raw_token,
        user_id=user_id,
        session_id=None,
        purpose=GrantPurpose.RESET_PASSWORD,
        action_class="password.reset",
        subject_hash=None,
    )
    with pytest.raises(AuthGrantError, match="unavailable"):
        service.consume(
            raw_token,
            user_id=user_id,
            session_id=None,
            purpose=GrantPurpose.RESET_PASSWORD,
            action_class="password.reset",
            subject_hash=None,
        )
