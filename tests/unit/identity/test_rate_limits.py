"""Test authentication rate-limit behavior."""

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.identity.rate_limits import (
    InMemoryRateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
)


def test_in_memory_rate_limit_returns_retry_after() -> None:
    """Reject requests beyond a fixed-window policy."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    limiter = InMemoryRateLimiter(clock=lambda: now)
    policy = RateLimitPolicy(limit=2, window=timedelta(seconds=60))
    limiter.check("otp:destination:value", policy)
    limiter.check("otp:destination:value", policy)
    with pytest.raises(RateLimitExceeded) as error:
        limiter.check("otp:destination:value", policy)
    assert error.value.retry_after_seconds == 60
