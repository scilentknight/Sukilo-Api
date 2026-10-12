"""Test OTP challenge lifecycle rules."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.core.security import DigestService
from app.modules.identity.otp import OtpError, OtpService
from app.modules.identity.types import OtpChannel, OtpPurpose


class OtpRepositoryFake:
    """Store OTP challenges for deterministic service tests."""

    def __init__(self) -> None:
        """Initialize empty storage."""
        self.rows = {}

    def invalidate_active(self, destination_hash, purpose, now):
        """Invalidate matching live challenges."""
        for row in self.rows.values():
            if row.destination_hash == destination_hash and row.purpose == purpose:
                row.invalidated_at = now

    def add(self, challenge):
        """Store a challenge."""
        self.rows[challenge.otp_id] = challenge

    def get_for_update(self, otp_id):
        """Return a challenge."""
        return self.rows.get(otp_id)

    def flush(self):
        """Accept staged changes."""


class NotificationFake:
    """Capture outgoing OTP notifications."""

    def __init__(self) -> None:
        """Initialize empty deliveries."""
        self.deliveries = []

    def send_otp(self, notification):
        """Capture an OTP notification."""
        self.deliveries.append(notification)


def _service(now: datetime) -> tuple[OtpService, OtpRepositoryFake, NotificationFake]:
    repository = OtpRepositoryFake()
    notifications = NotificationFake()
    service = OtpService(
        repository=repository,
        digests=DigestService(active_key_id="v1", peppers={"v1": b"pepper"}),
        notifications=notifications,
        clock=lambda: now,
        code_source=lambda: "123456",
        nonce_source=lambda: "random-nonce",
        ttl=timedelta(minutes=5),
        max_attempts=2,
    )
    return service, repository, notifications


def test_otp_is_delivered_but_never_stored_raw() -> None:
    """Persist only protected challenge values."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    service, repository, notifications = _service(now)
    otp_id = service.request(
        user_id=None,
        destination="buyer@example.com",
        channel=OtpChannel.EMAIL,
        purpose=OtpPurpose.RESET,
        locale_code="en-NP",
    )
    row = repository.rows[otp_id]
    assert notifications.deliveries[0].code == "123456"
    assert "123456" not in row.otp_hash
    assert "buyer@example.com" not in row.destination_hash


def test_otp_is_single_use_and_attempt_limited() -> None:
    """Reject invalid attempts and reuse after success."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    service, _, _ = _service(now)
    otp_id = service.request(
        user_id=UUID(int=1),
        destination="buyer@example.com",
        channel=OtpChannel.EMAIL,
        purpose=OtpPurpose.REGISTER,
        locale_code="en-NP",
    )
    with pytest.raises(OtpError, match="invalid"):
        service.verify(otp_id, OtpPurpose.REGISTER, "000000")
    service.verify(otp_id, OtpPurpose.REGISTER, "123456")
    with pytest.raises(OtpError, match="unavailable"):
        service.verify(otp_id, OtpPurpose.REGISTER, "123456")


def test_expected_user_mismatch_does_not_consume_challenge() -> None:
    """Reject a foreign owner before attempts or success are recorded."""
    now = datetime(2026, 10, 12, tzinfo=UTC)
    service, repository, _ = _service(now)
    otp_id = service.request(
        user_id=UUID(int=1),
        destination="new@example.com",
        channel=OtpChannel.EMAIL,
        purpose=OtpPurpose.VERIFY_EMAIL,
        locale_code="en-NP",
    )
    with pytest.raises(OtpError, match="unavailable"):
        service.verify(
            otp_id,
            OtpPurpose.VERIFY_EMAIL,
            "123456",
            expected_user_id=UUID(int=2),
        )
    row = repository.rows[otp_id]
    assert row.verified_at is None
    assert row.attempt_count == 0
    verified = service.verify(
        otp_id,
        OtpPurpose.VERIFY_EMAIL,
        "123456",
        expected_user_id=UUID(int=1),
    )
    assert verified.verified_at == now
