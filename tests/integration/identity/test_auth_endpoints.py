"""Test authentication HTTP cookie and CSRF contracts."""

from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.auth import get_identity_application, router
from app.core.database import get_db
from app.modules.identity.sessions import TokenPair


class DatabaseFake:
    """Record transaction boundary calls."""

    def commit(self) -> None:
        """Accept a transaction."""

    def rollback(self) -> None:
        """Reject a transaction."""


class AuthenticationFake:
    """Return a deterministic browser session."""

    def login(self, **kwargs) -> TokenPair:
        """Return login credentials."""
        return TokenPair("access-one", "refresh-one", UUID(int=1))


class SessionsFake:
    """Return deterministic rotated credentials."""

    def rotate(self, refresh_token: str) -> TokenPair:
        """Rotate the expected test refresh token."""
        assert refresh_token == "refresh-one"
        return TokenPair("access-two", "refresh-two", UUID(int=2))


class DigestsFake:
    """Return deterministic protected client fingerprints."""

    def digest(self, purpose: str, value: str) -> str:
        """Return a non-sensitive test digest."""
        return f"digest-{purpose}"


class ApplicationFake:
    """Expose authentication services used by tested routes."""

    authentication = AuthenticationFake()
    sessions = SessionsFake()
    digests = DigestsFake()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: DatabaseFake()
    app.dependency_overrides[get_identity_application] = lambda: ApplicationFake()
    return TestClient(app)


def test_login_sets_secure_refresh_and_csrf_cookies() -> None:
    """Keep refresh credentials out of the JSON response."""
    response = _client().post(
        "/api/v1/auth/login",
        json={
            "identifier": "buyer@example.com",
            "password": "password",
            "country_code": "NP",
        },
    )
    assert response.status_code == 200
    assert response.json()["access_token"] == "access-one"
    cookies = response.headers.get_list("set-cookie")
    assert any("sukilo_refresh=refresh-one" in value for value in cookies)
    assert any("HttpOnly" in value and "Secure" in value for value in cookies)
    assert all("Path=/api/v1/auth" in value for value in cookies)


def test_refresh_requires_origin_and_double_submit_csrf() -> None:
    """Reject cookie refresh without matching CSRF proof."""
    client = _client()
    client.cookies.set("sukilo_refresh", "refresh-one")
    client.cookies.set("sukilo_csrf", "csrf-value")
    rejected = client.post("/api/v1/auth/token/refresh")
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/v1/auth/token/refresh",
        headers={
            "Origin": "http://localhost:3000",
            "X-CSRF-Token": "csrf-value",
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["access_token"] == "access-two"
