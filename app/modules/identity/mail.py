"""Deliver authentication mail without logging message contents."""

import logging
import smtplib
from contextvars import ContextVar
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from app.modules.identity.notifications import OtpNotification, SecurityNotification
from app.modules.identity.types import OtpChannel, OtpPurpose

logger = logging.getLogger(__name__)
_development_code: ContextVar[str | None] = ContextVar("sukilo_development_otp", default=None)

_OTP_SUBJECTS = {
    OtpPurpose.REGISTER: "Your Sukilo verification code",
    OtpPurpose.RESET: "Your Sukilo password reset code",
    OtpPurpose.VERIFY_EMAIL: "Confirm your new Sukilo email",
    OtpPurpose.VERIFY_PHONE: "Your Sukilo verification code",
    OtpPurpose.STEP_UP: "Your Sukilo security code",
    OtpPurpose.DATA_EXPORT: "Your Sukilo data export code",
    OtpPurpose.LOGIN: "Your Sukilo sign-in code",
}


def clear_development_code() -> None:
    """Drop any code remembered for the current request."""
    _development_code.set(None)


def take_development_code() -> str | None:
    """Return and forget the code saved for local development."""
    code = _development_code.get()
    _development_code.set(None)
    return code


def _otp_message(notification: OtpNotification, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = _OTP_SUBJECTS.get(notification.purpose, "Your Sukilo code")
    message["From"] = sender
    message["To"] = notification.destination
    message.set_content(
        "\n".join(
            [
                f"Your Sukilo code is {notification.code}.",
                "It expires in a few minutes. If you did not request it, you can ignore this message.",
            ]
        )
    )
    return message


def _security_message(notification: SecurityNotification, sender: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Sukilo security notice"
    message["From"] = sender
    message["To"] = notification.destination
    message.set_content(
        f"A security event occurred on your Sukilo account: {notification.event_code}."
    )
    return message


class SmtpNotificationSender:
    """Send OTP and security messages through an SMTP server."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        starttls: bool,
        use_ssl: bool = False,
        client_factory=None,
    ) -> None:
        """Store connection settings. The password is never logged."""
        self.host = host
        self.port = port
        self.username = username
        self.password = (password or "").replace(" ", "") or None
        self.sender = sender
        self.starttls = starttls and not use_ssl
        self.use_ssl = use_ssl
        self.client_factory = client_factory or (
            smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        )

    def send_otp(self, notification: OtpNotification) -> None:
        """Email one OTP. SMS is not delivered by this adapter."""
        if notification.channel is not OtpChannel.EMAIL:
            logger.info(
                "otp_channel_unsupported purpose=%s channel=%s",
                notification.purpose.value,
                notification.channel.value,
            )
            return
        self._send(_otp_message(notification, self.sender), notification.purpose.value)

    def send_security(self, notification: SecurityNotification) -> None:
        """Email one security notice."""
        self._send(_security_message(notification, self.sender), notification.event_code)

    def _send(self, message: EmailMessage, event: str) -> None:
        try:
            with self.client_factory(self.host, self.port, timeout=15) as client:
                if self.starttls:
                    client.starttls()
                if self.username:
                    client.login(self.username, self.password or "")
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            logger.info("smtp_delivery_failed event=%s", event)
            raise RuntimeError("Email delivery failed.") from exc
        logger.info("smtp_delivery_queued event=%s", event)


class DevelopmentOutboxSender:
    """Save development mail as files when no SMTP server is configured."""

    def __init__(self, directory: Path) -> None:
        """Store the outbox directory. It is not served by the API."""
        self.directory = directory

    def send_otp(self, notification: OtpNotification) -> None:
        """Write one OTP message. The code stays in the file, not the log."""
        if notification.channel is not OtpChannel.EMAIL:
            logger.info(
                "otp_channel_unsupported purpose=%s channel=%s",
                notification.purpose.value,
                notification.channel.value,
            )
            return
        self._write(_otp_message(notification, "sukilo-dev@localhost"), notification.purpose.value)
        remember_development_code(notification.code)

    def send_security(self, notification: SecurityNotification) -> None:
        """Write one security notice."""
        self._write(
            _security_message(notification, "sukilo-dev@localhost"),
            notification.event_code,
        )

    def _write(self, message: EmailMessage, event: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        path = self.directory / f"{stamp}-{event}.eml"
        path.write_text(message.as_string(), encoding="utf-8")
        logger.info("development_mail_saved event=%s", event)


def remember_development_code(code: str) -> None:
    """Keep one development code available to the request that created it."""
    _development_code.set(code)
