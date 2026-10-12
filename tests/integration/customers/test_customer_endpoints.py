"""Test customer `/me` HTTP contracts and transaction boundaries."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.customers import get_customer_application, router
from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated
from app.modules.customers.identifier_change import (
    IdentifierChangeConflictError,
    IdentifierChangeInvalidError,
    IdentifierChangeUnavailableError,
    IdentifierChangeValidationError,
)
from app.modules.customers.profile import (
    CurrentSessionView,
    ProfileUnavailableError,
    ProfileView,
    mask_email,
)
from app.modules.customers.service import (
    AddressConflictError,
    AddressNotFoundError,
    AddressValidationError,
    AddressView,
    ConsentHistoryPage,
    ConsentValidationError,
    ConsentView,
    PreferenceConflictError,
    PreferenceValidationError,
    PreferenceView,
)
from app.modules.customers.types import AddressType, ConsentSource, ConsentType, DefaultKind
from app.modules.identity.types import AccountStatus


NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
USER_ID = UUID(int=1)
OTHER_USER_ID = UUID(int=2)
SESSION_ID = UUID(int=3)
CORRELATION_ID = UUID(int=4)
COUNTRY_ID = UUID(int=5)
CORRELATION_HEADER = "X-Correlation-ID"


def _context(
    user_id: UUID = USER_ID,
    session_id: UUID = SESSION_ID,
) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        session_id=session_id,
        auth_version=1,
        authentication_methods=("password",),
        authenticated_at=NOW,
        correlation_id=CORRELATION_ID,
    )


class DatabaseFake:
    """Record transaction boundaries."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


class ProfileFake:
    """Return a deterministic masked profile."""

    def get_profile(self, actor: AuthContext) -> object:
        if actor.user_id != USER_ID:
            raise ProfileUnavailableError("Profile is unavailable.")
        return ProfileView(
            user_id=USER_ID,
            status=AccountStatus.ACTIVE,
            email_masked=mask_email("buyer@example.com"),
            phone_masked="+***678",
            email_verified=True,
            phone_verified=False,
            created_at=NOW,
            last_login_at=NOW,
            preferences=None,
            current_session=CurrentSessionView(
                session_id=SESSION_ID,
                device_id="device",
                device_name="Browser",
                issued_at=NOW,
                is_current=True,
            ),
        )


class IdentifierChangesFake:
    """Capture identifier-change calls."""

    def __init__(self) -> None:
        self.requested = False
        self.confirmed = False

    def request_change(self, command: object) -> UUID:
        self.requested = True
        if isinstance(command, object) and getattr(command, "new_email", None) == "taken@example.com":
            raise IdentifierChangeConflictError("Identifier change cannot be completed.")
        return UUID(int=99)

    def confirm_change(self, command: object) -> None:
        self.confirmed = True
        grant = getattr(command, "grant", "")
        if grant.startswith("invalid"):
            raise IdentifierChangeInvalidError("Authorization grant is invalid.")
        if grant == "stale-password":
            raise IdentifierChangeUnavailableError("Recent password authentication is required.")


class PreferencesFake:
    """Return and update preference views."""

    version = 2

    def get_preferences(self, actor: AuthContext) -> PreferenceView | None:
        if actor.user_id != USER_ID:
            return None
        return PreferenceView(
            user_id=USER_ID,
            country_id=COUNTRY_ID,
            country_code="NP",
            locale_code="en-NP",
            currency_code="NPR",
            marketing_email=False,
            marketing_sms=False,
            version=self.version,
        )

    def set_preferences(self, command: object) -> PreferenceView:
        expected = getattr(command, "expected_version", None)
        if expected != self.version:
            raise PreferenceConflictError("Preference version is stale.")
        return self.get_preferences(_context())


class ConsentsFake:
    """Return scoped consent pages."""

    def list_current(self, actor: AuthContext, country_code: str) -> list[ConsentView]:
        if country_code == "XX":
            raise ConsentValidationError("Country is unavailable.")
        if actor.user_id != OTHER_USER_ID:
            return [_consent_view()]
        return []

    def list_history(
        self,
        actor: AuthContext,
        country_code: str,
        *,
        limit: int,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> ConsentHistoryPage:
        if country_code == "XX":
            raise ConsentValidationError("Country is unavailable.")
        if actor.user_id != USER_ID:
            return ConsentHistoryPage(items=[], next_cursor=None)
        return ConsentHistoryPage(items=[_consent_view()], next_cursor=None)

    def record(self, command: object) -> ConsentView:
        if getattr(command, "policy_version", "") != "dev-1":
            raise ConsentValidationError("Consent policy version is stale.")
        return _consent_view()


class AddressesFake:
    """Simulate scoped address operations."""

    address_id = UUID(int=8)
    version = 1

    def list_addresses(self, actor: AuthContext, country_code: str) -> list[AddressView]:
        if country_code == "XX":
            raise AddressValidationError("Country is unavailable.")
        if actor.user_id != USER_ID or country_code != "NP":
            return []
        return [_address_view()]

    def create_address(self, command: object) -> AddressView:
        return _address_view()

    def get_address(
        self,
        actor: AuthContext,
        country_code: str,
        address_id: UUID,
    ) -> AddressView:
        if country_code == "XX":
            raise AddressValidationError("Country is unavailable.")
        if actor.user_id != USER_ID or address_id != self.address_id:
            raise AddressNotFoundError("Address is unavailable.")
        return _address_view()

    def update_address(self, command: object) -> AddressView:
        if getattr(command, "expected_version", None) != self.version:
            raise AddressConflictError("Address version is stale.")
        return _address_view(version=self.version + 1)

    def delete_address(
        self,
        actor: AuthContext,
        country_code: str,
        address_id: UUID,
        expected_version: int,
    ) -> None:
        if expected_version != self.version:
            raise AddressConflictError("Address version is stale.")

    def set_default(
        self,
        actor: AuthContext,
        country_code: str,
        address_id: UUID,
        kind: DefaultKind,
    ) -> AddressView:
        if actor.user_id != USER_ID:
            raise AddressNotFoundError("Address is unavailable.")
        return _address_view(is_default_shipping=True)


class ApplicationFake:
    """Expose customer services used by tested routes."""

    def __init__(self) -> None:
        self.profile = ProfileFake()
        self.identifier_changes = IdentifierChangesFake()
        self.preferences = PreferencesFake()
        self.consents = ConsentsFake()
        self.addresses = AddressesFake()


def _consent_view() -> ConsentView:
    return ConsentView(
        consent_id=UUID(int=6),
        user_id=USER_ID,
        country_id=COUNTRY_ID,
        consent_type=ConsentType.TERMS,
        policy_version="dev-1",
        granted=True,
        source=ConsentSource.WEB,
        recorded_at=NOW,
        correlation_id=CORRELATION_ID,
    )


def _address_view(**overrides: object) -> AddressView:
    values = {
        "address_id": UUID(int=8),
        "user_id": USER_ID,
        "country_id": COUNTRY_ID,
        "geography_area_id": None,
        "recipient_name": "Recipient",
        "phone_e164": "+9779841234567",
        "address_line_1": "Line 1",
        "address_line_2": None,
        "landmark": None,
        "city_text": "Kathmandu",
        "postal_code": None,
        "latitude": Decimal("27.7"),
        "longitude": Decimal("85.3"),
        "address_type": AddressType.HOME,
        "is_shipping": True,
        "is_billing": False,
        "is_default_shipping": False,
        "is_default_billing": False,
        "created_at": NOW,
        "updated_at": NOW,
        "version": 1,
    }
    values.update(overrides)
    return AddressView(**values)


def _client(
    application: ApplicationFake | None = None,
    database: DatabaseFake | None = None,
    actor: AuthContext | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    application = application or ApplicationFake()
    database = database or DatabaseFake()
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_customer_application] = lambda: application
    app.dependency_overrides[require_authenticated] = lambda: actor or _context()
    return TestClient(app)


def test_get_me_masks_identifiers_and_commits_read() -> None:
    """Return a safe profile without mutating state."""
    database = DatabaseFake()
    response = _client(database=database).get("/api/v1/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email_masked"].startswith("b")
    assert "buyer@example.com" not in body["email_masked"]
    assert "password_hash" not in body
    assert database.events == []


def test_patch_me_starts_identifier_change_without_apply() -> None:
    """Start one OTP challenge and leave the account unchanged."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).patch(
        "/api/v1/me",
        json={
            "new_email": "new@example.com",
            "country_code": "NP",
            "locale_code": "en-NP",
        },
    )
    assert response.status_code == 202
    assert response.json()["challenge_id"] == str(UUID(int=99))
    assert application.identifier_changes.requested is True
    assert application.identifier_changes.confirmed is False
    assert database.events == ["commit"]


def test_confirm_identifier_change_commits_on_success() -> None:
    """Consume a grant and commit identifier replacement."""
    application, database = ApplicationFake(), DatabaseFake()
    response = _client(application, database).post(
        "/api/v1/me/identifier-change/confirm",
        json={
            "grant": "opaque-grant-token-with-sufficient-length",
            "new_email": "new@example.com",
        },
    )
    assert response.status_code == 204
    assert application.identifier_changes.confirmed is True
    assert database.events == ["commit"]


def test_confirm_identifier_change_rolls_back_invalid_grant() -> None:
    """Map invalid grants to a safe client error."""
    database = DatabaseFake()
    response = _client(database=database).post(
        "/api/v1/me/identifier-change/confirm",
        json={
            "grant": "invalid-grant-token-with-sufficient-length",
            "new_email": "new@example.com",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CUSTOMER_IDENTIFIER_GRANT_INVALID"
    assert database.events == ["rollback"]


def test_preference_update_rejects_extra_fields() -> None:
    """Reject unknown preference keys."""
    response = _client().put(
        "/api/v1/me/preferences",
        json={
            "country_code": "NP",
            "locale_code": "en-NP",
            "currency_code": "NPR",
            "expected_version": 2,
            "marketing_email": True,
        },
    )
    assert response.status_code == 422


def test_preference_update_conflict_returns_version_code() -> None:
    """Return a stable conflict code for stale preference writes."""
    database = DatabaseFake()
    response = _client(database=database).put(
        "/api/v1/me/preferences",
        json={
            "country_code": "NP",
            "locale_code": "en-NP",
            "currency_code": "NPR",
            "expected_version": 1,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CUSTOMER_VERSION_CONFLICT"
    assert database.events == ["rollback"]


def test_consent_history_returns_items_and_next_cursor() -> None:
    """Paginate consent evidence with an opaque cursor."""
    response = _client().get(
        "/api/v1/me/consents",
        params={"country_code": "NP", "history": "true", "limit": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] is None


def test_consent_history_isolates_owner_scope() -> None:
    """Never return another owner's consent page."""
    application = ApplicationFake()
    client = _client(application, actor=_context(user_id=OTHER_USER_ID))
    response = client.get(
        "/api/v1/me/consents",
        params={"country_code": "NP", "history": "true", "limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_address_update_conflict_returns_version_code() -> None:
    """Return a stable conflict code for stale address writes."""
    database = DatabaseFake()
    response = _client(database=database).patch(
        f"/api/v1/me/addresses/{UUID(int=8)}",
        params={"country_code": "NP"},
        json=_address_update_payload(expected_version=99),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CUSTOMER_VERSION_CONFLICT"
    assert database.events == ["rollback"]


def test_address_update_rejects_country_code_in_json_body() -> None:
    """Keep address updates scoped only through the query country."""
    response = _client().patch(
        f"/api/v1/me/addresses/{UUID(int=8)}",
        params={"country_code": "NP"},
        json={**_address_update_payload(), "country_code": "NP"},
    )
    assert response.status_code == 422


def test_invalid_country_on_consent_list_returns_safe_bad_request() -> None:
    """Map invalid consent country resolution to a safe client error."""
    response = _client().get("/api/v1/me/consents", params={"country_code": "XX"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Request could not be completed."


def test_invalid_country_on_address_list_returns_safe_bad_request() -> None:
    """Map invalid address country resolution to a safe client error."""
    response = _client().get("/api/v1/me/addresses", params={"country_code": "XX"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Request could not be completed."


def test_invalid_country_on_address_get_returns_bad_request_not_server_error() -> None:
    """Validate address country before ownership checks."""
    response = _client().get(
        f"/api/v1/me/addresses/{UUID(int=8)}",
        params={"country_code": "XX"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Request could not be completed."


def test_mutating_success_includes_correlation_header() -> None:
    """Expose the actor correlation identifier on successful mutations."""
    response = _client().put(
        "/api/v1/me/preferences",
        json={
            "country_code": "NP",
            "locale_code": "en-NP",
            "currency_code": "NPR",
            "expected_version": 2,
        },
    )
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER] == str(CORRELATION_ID)


def test_mutating_error_includes_correlation_header() -> None:
    """Expose the actor correlation identifier on known mutation failures."""
    response = _client().put(
        "/api/v1/me/preferences",
        json={
            "country_code": "NP",
            "locale_code": "en-NP",
            "currency_code": "NPR",
            "expected_version": 1,
        },
    )
    assert response.status_code == 409
    assert response.headers[CORRELATION_HEADER] == str(CORRELATION_ID)


def test_identifier_confirm_success_includes_correlation_header() -> None:
    """Expose correlation on a no-content identifier confirmation."""
    response = _client().post(
        "/api/v1/me/identifier-change/confirm",
        json={
            "grant": "opaque-grant-token-with-sufficient-length",
            "new_email": "new@example.com",
        },
    )
    assert response.status_code == 204
    assert response.headers[CORRELATION_HEADER] == str(CORRELATION_ID)


def test_cross_owner_address_lookup_returns_not_found() -> None:
    """Hide address existence from other owners."""
    response = _client(actor=_context(user_id=OTHER_USER_ID)).get(
        f"/api/v1/me/addresses/{UUID(int=8)}",
        params={"country_code": "NP"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Address is unavailable."


def _address_update_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "recipient_name": "Recipient",
        "phone": "+9779841234567",
        "address_line_1": "Line 1",
        "address_type": "HOME",
        "is_shipping": True,
        "is_billing": False,
        "is_default_shipping": False,
        "is_default_billing": False,
        "expected_version": 1,
    }
    payload.update(overrides)
    return payload
