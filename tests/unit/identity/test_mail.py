"""Test OTP mail delivery without logging the code."""

from email.message import EmailMessage

from app.core.config import Settings
from app.modules.identity.bootstrap import build_notification_sender
from app.modules.identity.mail import (
    DevelopmentOutboxSender,
    SmtpNotificationSender,
    clear_development_code,
    take_development_code,
)
from app.modules.identity.notifications import OtpNotification
from app.modules.identity.types import OtpChannel, OtpPurpose


class FakeSmtp:
    """Record one SMTP session without opening a socket."""

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        """Store connection arguments."""
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started = False
        self.logged_in = None
        self.messages: list[EmailMessage] = []

    def __enter__(self) -> "FakeSmtp":
        """Open the fake session."""
        return self

    def __exit__(self, *args: object) -> bool:
        """Close the fake session."""
        del args
        return False

    def starttls(self) -> None:
        """Record STARTTLS."""
        self.started = True

    def login(self, username: str, password: str) -> None:
        """Record credentials without writing them to a log."""
        self.logged_in = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        """Record the message."""
        self.messages.append(message)


def _otp() -> OtpNotification:
    return OtpNotification(
        destination="e2e-signup@example.com",
        channel=OtpChannel.EMAIL,
        purpose=OtpPurpose.REGISTER,
        code="246810",
        locale_code="en",
    )


def test_smtp_sender_delivers_the_code_in_the_message(tmp_path) -> None:
    """Put the code in the email body and keep it out of the filename."""
    del tmp_path
    clients = []

    def factory(host: str, port: int, timeout: int | None = None) -> FakeSmtp:
        client = FakeSmtp(host, port, timeout)
        clients.append(client)
        return client

    sender = SmtpNotificationSender(
        host="smtp.example",
        port=587,
        username="mailer",
        password="secret-value",
        sender="noreply@example.com",
        starttls=True,
        client_factory=factory,
    )
    clear_development_code()
    sender.send_otp(_otp())
    body = clients[0].messages[0].get_content()
    assert "246810" in body
    assert take_development_code() is None
    assert clients[0].started is True
    assert clients[0].logged_in == ("mailer", "secret-value")


def test_development_outbox_saves_the_code_in_the_file(tmp_path) -> None:
    """Save a development message whose filename does not contain the code."""
    clear_development_code()
    sender = DevelopmentOutboxSender(tmp_path)
    sender.send_otp(_otp())
    assert take_development_code() == "246810"
    files = list(tmp_path.glob("*.eml"))
    assert len(files) == 1
    assert "246810" not in files[0].name
    assert "246810" in files[0].read_text(encoding="utf-8")


def test_missing_smtp_uses_the_development_outbox() -> None:
    """Keep local registration testable when no SMTP server is configured."""
    settings = Settings(
        DATABASE_URL="postgresql+psycopg://user:pass@localhost/sukilo",
        APP_ENV="development",
        ENABLE_VECTOR_STACK=False,
        SMTP_HOST=None,
        SMTP_FROM=None,
    )
    sender = build_notification_sender(settings)
    assert isinstance(sender, DevelopmentOutboxSender)
