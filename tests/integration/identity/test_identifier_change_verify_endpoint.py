"""Test identifier-change OTP verification over the auth route."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.auth import get_identity_application, router
from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.identity.otp import OtpError
from app.modules.identity.types import OtpPurpose


class DatabaseFake:
    """Record transaction outcomes."""

    def __init__(self) -> None:
        """Initialize empty outcomes."""
        self.events: list[str] = []

    def commit(self) -> None:
        """Record a commit."""
        self.events.append("commit")

    def rollback(self) -> None:
        """Record a rollback."""
        self.events.append("rollback")


class ApplicationFake:
    """Expose only identifier-change verification collaborators."""

    def __init__(self) -> None:
        """Initialize captured calls."""
        self.calls: list[dict[str, object]] = []

    def authenticate(self, token: str, correlation_id: UUID) -> AuthContext:
        """Return a context for the expected token."""
        assert token == "access-token"
        return AuthContext(
            user_id=UUID(int=1),
            session_id=UUID(int=2),
            auth_version=1,
            authentication_methods=("password",),
            authenticated_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )

    def verify_identifier_change(self, **kwargs: object) -> str:
        """Capture arguments and return an opaque grant."""
        self.calls.append(kwargs)
        if kwargs["purpose"] == OtpPurpose.VERIFY_PHONE:
            raise OtpError("OTP challenge is unavailable.")
        return "opaque-change-grant"

    def verify_data_export(self, **kwargs: object) -> str:
        """Capture an authenticated export verification."""
        self.calls.append(kwargs)
        return "opaque-data-export-grant"


def _client(application: ApplicationFake, database: DatabaseFake) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_identity_application] = lambda: application
    return TestClient(app)


def _payload(purpose: str) -> dict[str, str]:
    return {"challenge_id": str(uuid4()), "purpose": purpose, "code": "123456"}


def test_verify_email_otp_returns_grant_for_authenticated_session() -> None:
    """Exchange an owned candidate OTP for a one-time grant."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).post(
        "/api/v1/auth/otp/verify",
        headers={"Authorization": "Bearer access-token"},
        json=_payload("VERIFY_EMAIL"),
    )
    assert response.status_code == 200
    assert response.json()["grant"] == "opaque-change-grant"
    assert application.calls[0]["context"].session_id == UUID(int=2)
    assert application.calls[0]["purpose"] == OtpPurpose.VERIFY_EMAIL
    assert database.events == ["commit"]


def test_verify_candidate_otp_requires_bearer_authentication() -> None:
    """Reject identifier verification without an authenticated session."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).post(
        "/api/v1/auth/otp/verify",
        json=_payload("VERIFY_EMAIL"),
    )
    assert response.status_code == 400
    assert application.calls == []
    assert database.events == ["rollback"]


def test_verify_candidate_otp_failure_is_safe_and_rolled_back() -> None:
    """Map OTP failures to a generic client error."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).post(
        "/api/v1/auth/otp/verify",
        headers={"Authorization": "Bearer access-token"},
        json=_payload("VERIFY_PHONE"),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "OTP challenge is unavailable."
    assert database.events == ["rollback"]


def test_verify_data_export_otp_requires_authenticated_session() -> None:
    """Exchange an owned export OTP only on the authenticated path."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).post(
        "/api/v1/auth/otp/verify",
        headers={"Authorization": "Bearer access-token"},
        json=_payload("DATA_EXPORT"),
    )
    assert response.status_code == 200
    assert response.json()["grant"] == "opaque-data-export-grant"
    assert application.calls[0]["context"].session_id == UUID(int=2)
    assert database.events == ["commit"]


def test_public_otp_request_does_not_enable_data_export() -> None:
    """Keep data-export OTP creation off the public request route."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).post(
        "/api/v1/auth/otp/request",
        json={
            "identifier": "buyer@example.com",
            "purpose": "DATA_EXPORT",
            "country_code": "NP",
            "locale_code": "en-NP",
        },
    )
    assert response.status_code == 400
    assert database.events == []
