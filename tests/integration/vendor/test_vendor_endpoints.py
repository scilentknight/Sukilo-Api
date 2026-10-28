"""Test seller HTTP responses hide document storage keys."""

import base64
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.vendor_applications import router
from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated
from app.modules.vendor.applications import DocumentView
from app.modules.vendor.dependencies import get_application_service
from app.modules.vendor.types import DocumentType, VerificationStatus

NOW = datetime(2026, 10, 28, tzinfo=UTC)


class ApplicationService:
    """Return a document view with no storage key."""

    def add_document(self, actor: AuthContext, vendor_country_id, **kwargs: object) -> DocumentView:
        """Return public document metadata."""
        del actor, vendor_country_id, kwargs
        return DocumentView(uuid4(), DocumentType.REGISTRATION, VerificationStatus.PENDING)

    def get_document(self, actor: AuthContext, vendor_country_id, vendor_document_id) -> DocumentView:
        """Hide the requested document."""
        del actor, vendor_country_id, vendor_document_id
        from app.modules.vendor.errors import VendorServiceError
        raise VendorServiceError(404, "Document is unavailable.")


def _client() -> TestClient:
    """Build a client with the application route and a fake service."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_authenticated] = lambda: AuthContext(
        uuid4(),
        uuid4(),
        1,
        ("password",),
        NOW,
        uuid4(),
    )
    app.dependency_overrides[get_db] = lambda: type(
        "Database",
        (),
        {"commit": lambda self: None, "rollback": lambda self: None},
    )()
    app.dependency_overrides[get_application_service] = lambda: ApplicationService()
    return TestClient(app)


def test_document_response_omits_storage_key() -> None:
    """Omit storage_key from the document creation response."""
    response = _client().post(
        f"/api/v1/vendor/applications/{uuid4()}/documents",
        json={
            "document_type": "REGISTRATION",
            "document_number": "REG-SECRET",
            "content_base64": base64.b64encode(b"certificate").decode(),
            "media_type": "application/pdf",
        },
    )
    assert response.status_code == 201
    assert "storage_key" not in response.json()
    assert "REG-SECRET" not in response.text


def test_guessed_document_route_returns_not_found() -> None:
    """Return 404 when the document service hides the identifier."""
    response = _client().get(f"/api/v1/vendor/applications/{uuid4()}/documents/{uuid4()}")
    assert response.status_code == 404
