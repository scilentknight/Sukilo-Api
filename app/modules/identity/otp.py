"""Create and verify short-lived OTP challenges."""

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from app.core.security import DigestService
from app.modules.identity.events import SecurityEventRecorder
from app.modules.identity.models import OtpChallenge
from app.modules.identity.notifications import NotificationSender, OtpNotification
from app.modules.identity.rate_limits import RateLimitPolicy, RateLimiter
from app.modules.identity.types import OtpChannel, OtpPurpose


class OtpRepositoryProtocol(Protocol):
    """Describe OTP challenge persistence."""

    def invalidate_active(
        self,
        destination_hash: str,
        purpose: OtpPurpose,
        now: datetime,
    ) -> None:
        """Invalidate matching active challenges."""
        ...

    def add(self, challenge: OtpChallenge) -> None:
        """Stage a challenge."""
        ...

    def get_for_update(self, otp_id: UUID) -> OtpChallenge | None:
        """Lock and return a challenge."""
        ...

    def flush(self) -> None:
        """Flush staged changes."""
        ...


class OtpError(Exception):
    """Report an enumeration-safe OTP failure."""


class OtpCodeInvalidError(OtpError):
    """Report an invalid code whose persisted attempt must be committed."""


class OtpService:
    """Manage secure single-use OTP challenges."""

    def __init__(
        self,
        *,
        repository: OtpRepositoryProtocol,
        digests: DigestService,
        notifications: NotificationSender,
        clock: Callable[[], datetime],
        code_source: Callable[[], str] | None = None,
        nonce_source: Callable[[], str] | None = None,
        ttl: timedelta = timedelta(minutes=5),
        max_attempts: int = 5,
        rate_limiter: RateLimiter | None = None,
        events: SecurityEventRecorder | None = None,
    ) -> None:
        """Initialize OTP dependencies and policy."""
        self.repository = repository
        self.digests = digests
        self.notifications = notifications
        self.clock = clock
        self.code_source = code_source or _generate_code
        self.nonce_source = nonce_source or _generate_nonce
        self.ttl = ttl
        self.max_attempts = max_attempts
        self.rate_limiter = rate_limiter
        self.events = events

    def request(
        self,
        *,
        user_id: UUID | None,
        destination: str,
        channel: OtpChannel,
        purpose: OtpPurpose,
        locale_code: str,
    ) -> UUID:
        """Create, persist, and deliver one OTP challenge."""
        now = self.clock()
        otp_id = uuid4()
        nonce = self.nonce_source()
        code = self.code_source()
        destination_hash = self.digests.digest("otp-destination", destination)
        if self.rate_limiter is not None:
            self.rate_limiter.check(
                f"otp:request:destination:{destination_hash}",
                RateLimitPolicy(limit=5, window=timedelta(minutes=15)),
            )
        otp_hash = self.digests.digest(
            "otp-code",
            str(otp_id),
            nonce,
            purpose.value,
            code,
        )
        self.repository.invalidate_active(destination_hash, purpose, now)
        challenge = OtpChallenge(
            otp_id=otp_id,
            user_id=user_id,
            destination_hash=destination_hash,
            otp_nonce=nonce,
            channel=channel,
            purpose=purpose,
            otp_hash=otp_hash,
            attempt_count=0,
            max_attempts=self.max_attempts,
            expires_at=now + self.ttl,
            created_at=now,
        )
        self.repository.add(challenge)
        self.repository.flush()
        if self.events is not None:
            self.events.record(
                action="iam.otp.requested",
                resource_type="otp_challenge",
                resource_id=otp_id,
                actor_user_id=user_id,
                after_data={"purpose": purpose.value, "channel": channel.value},
            )
        self.notifications.send_otp(
            OtpNotification(
                destination=destination,
                channel=channel,
                purpose=purpose,
                code=code,
                locale_code=locale_code,
            )
        )
        return otp_id

    def verify(
        self,
        otp_id: UUID,
        purpose: OtpPurpose,
        code: str,
        *,
        expected_user_id: UUID | None = None,
    ) -> OtpChallenge:
        """Consume a valid OTP challenge exactly once.

        Args:
            otp_id: Opaque challenge identifier.
            purpose: Purpose the caller requires.
            code: Raw one-time code supplied by the user.
            expected_user_id: Owner the challenge must belong to. A mismatch
                is rejected before any attempt or success is recorded.

        Returns:
            The verified challenge.

        Raises:
            OtpCodeInvalidError: If the code is invalid.
            OtpError: If the challenge is unavailable.
        """
        challenge = self.repository.get_for_update(otp_id)
        now = self.clock()
        if self.rate_limiter is not None:
            self.rate_limiter.check(
                f"otp:verify:challenge:{otp_id}",
                RateLimitPolicy(limit=self.max_attempts, window=self.ttl),
            )
        if challenge is None or challenge.purpose != purpose:
            raise OtpError("OTP challenge is unavailable.")
        if expected_user_id is not None and challenge.user_id != expected_user_id:
            raise OtpError("OTP challenge is unavailable.")
        if (
            challenge.verified_at is not None
            or challenge.invalidated_at is not None
            or challenge.expires_at <= now
            or challenge.attempt_count >= challenge.max_attempts
        ):
            raise OtpError("OTP challenge is unavailable.")
        valid = self.digests.verify(
            challenge.otp_hash,
            "otp-code",
            str(challenge.otp_id),
            challenge.otp_nonce,
            challenge.purpose.value,
            code,
        )
        if not valid:
            challenge.attempt_count += 1
            self.repository.flush()
            raise OtpCodeInvalidError("OTP code is invalid.")
        challenge.verified_at = now
        if self.events is not None:
            self.events.record(
                action="iam.otp.verified",
                resource_type="otp_challenge",
                resource_id=challenge.otp_id,
                actor_user_id=challenge.user_id,
                after_data={"purpose": challenge.purpose.value},
            )
        self.repository.flush()
        return challenge


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _generate_nonce() -> str:
    return secrets.token_urlsafe(24)
