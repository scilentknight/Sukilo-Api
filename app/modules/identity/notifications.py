"""Define security notification contracts."""

from dataclasses import dataclass
from typing import Protocol

from app.modules.identity.types import OtpChannel, OtpPurpose


@dataclass(frozen=True, slots=True)
class OtpNotification:
    """Describe an OTP delivery without persistence concerns."""

    destination: str
    channel: OtpChannel
    purpose: OtpPurpose
    code: str
    locale_code: str


@dataclass(frozen=True, slots=True)
class SecurityNotification:
    """Describe a PII-safe account security notification."""

    destination: str
    event_code: str
    locale_code: str


class NotificationSender(Protocol):
    """Deliver authentication and account security notifications."""

    def send_otp(self, notification: OtpNotification) -> None:
        """Deliver one OTP notification."""
        ...

    def send_security(self, notification: SecurityNotification) -> None:
        """Deliver one account security notification."""
        ...
