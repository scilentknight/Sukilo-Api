"""Enforce atomic fixed-window authentication rate limits."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Define a fixed-window request limit."""

    limit: int
    window: timedelta


class RateLimitExceeded(Exception):
    """Report a rate limit with retry timing."""

    def __init__(self, retry_after_seconds: int) -> None:
        """Initialize a rate-limit rejection.

        Args:
            retry_after_seconds: Seconds until another attempt is allowed.
        """
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Authentication request rate limit exceeded.")


class RateLimiter(Protocol):
    """Check distributed authentication limits."""

    def check(self, key: str, policy: RateLimitPolicy) -> None:
        """Consume one allowance or raise a rate-limit error."""
        ...


class RedisClientProtocol(Protocol):
    """Describe the Redis operation required for atomic limits."""

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> list[int]:
        """Evaluate a server-side rate-limit script."""
        ...


_FIXED_WINDOW_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('PTTL', KEYS[1])
return {count, ttl}
"""


class RedisRateLimiter:
    """Apply atomic counters through shared Redis storage."""

    def __init__(self, client: RedisClientProtocol, prefix: str = "iam:rl") -> None:
        """Initialize the Redis limiter."""
        self.client = client
        self.prefix = prefix

    def check(self, key: str, policy: RateLimitPolicy) -> None:
        """Consume one allowance or raise with Redis TTL."""
        window_ms = max(1, math.ceil(policy.window.total_seconds() * 1000))
        count, ttl_ms = self.client.eval(
            _FIXED_WINDOW_SCRIPT,
            1,
            f"{self.prefix}:{key}",
            window_ms,
        )
        if count > policy.limit:
            raise RateLimitExceeded(max(1, math.ceil(ttl_ms / 1000)))


class InMemoryRateLimiter:
    """Apply deterministic limits for single-process development and tests."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        """Initialize local counter storage."""
        self.clock = clock
        self._windows: dict[str, tuple[int, datetime]] = {}

    def check(self, key: str, policy: RateLimitPolicy) -> None:
        """Consume one local allowance or raise with remaining window."""
        now = self.clock()
        count, expires_at = self._windows.get(
            key,
            (0, now + policy.window),
        )
        if now >= expires_at:
            count, expires_at = 0, now + policy.window
        count += 1
        self._windows[key] = (count, expires_at)
        if count > policy.limit:
            remaining = max(1, math.ceil((expires_at - now).total_seconds()))
            raise RateLimitExceeded(remaining)
