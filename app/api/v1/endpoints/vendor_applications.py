"""Expose seller application routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated
from app.modules.vendor.applications import ApplicationView, DocumentView, VendorApplicationService
from app.modules.vendor.dependencies import commit_vendor, get_application_service
from app.modules.vendor.schemas import (
    ApplicationCreateRequest,
    ApplicationResponse,
    ApplicationUpdateRequest,
    DocumentCreateRequest,
    DocumentResponse,
    SubmitApplicationRequest,
)
from sqlalchemy.orm import Session

router = APIRouter(prefix="/vendor", tags=["Vendor"])


@router.post(
    "/applications",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_application(
    payload: ApplicationCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorApplicationService, Depends(get_application_service)],
) -> ApplicationResponse:
    """Create a draft seller application for one country."""
    view = commit_vendor(
        db,
        lambda: service.create_draft(
            context,
            country_code=payload.country_code,
            legal_name=payload.legal_name,
            display_name=payload.display_name,
            vendor_type=payload.vendor_type,
            vendor_id=payload.vendor_id,
        ),
    )
    return _application_response(view)


@router.patch("/applications/{vendor_country_id}", response_model=ApplicationResponse)
def update_application(
    vendor_country_id: UUID,
    payload: ApplicationUpdateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorApplicationService, Depends(get_application_service)],
) -> ApplicationResponse:
    """Update a draft or review application."""
    view = commit_vendor(
        db,
        lambda: service.update_draft(
            context,
            vendor_country_id,
            expected_version=payload.expected_version,
            legal_name=payload.legal_name,
            display_name=payload.display_name,
            vendor_type=payload.vendor_type,
        ),
    )
    return _application_response(view)


@router.post(
    "/applications/{vendor_country_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_document(
    vendor_country_id: UUID,
    payload: DocumentCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorApplicationService, Depends(get_application_service)],
) -> DocumentResponse:
    """Store document metadata and hide the storage key."""
    view = commit_vendor(
        db,
        lambda: service.add_document(
            context,
            vendor_country_id,
            document_type=payload.document_type,
            document_number=payload.document_number,
            content_base64=payload.content_base64,
            media_type=payload.media_type,
        ),
    )
    return _document_response(view)


@router.post(
    "/applications/{vendor_country_id}/submit",
    response_model=ApplicationResponse,
)
def submit_application(
    vendor_country_id: UUID,
    payload: SubmitApplicationRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorApplicationService, Depends(get_application_service)],
) -> ApplicationResponse:
    """Submit one country application without granting a seller badge."""
    view = commit_vendor(
        db,
        lambda: service.submit(
            context,
            vendor_country_id,
            terms_version=payload.terms_version,
        ),
    )
    return _application_response(view)


@router.get("/applications/{vendor_country_id}", response_model=ApplicationResponse)
def read_application(
    vendor_country_id: UUID,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorApplicationService, Depends(get_application_service)],
) -> ApplicationResponse:
    """Return an application without storage keys."""
    view = commit_vendor(db, lambda: service.get_application(context, vendor_country_id))
    return _application_response(view)


@router.get(
    "/applications/{vendor_country_id}/documents/{vendor_document_id}",
    response_model=DocumentResponse,
)
def read_document(
    vendor_country_id: UUID,
    vendor_document_id: UUID,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorApplicationService, Depends(get_application_service)],
) -> DocumentResponse:
    """Return document metadata or hide a guessed identifier."""
    view = commit_vendor(
        db,
        lambda: service.get_document(context, vendor_country_id, vendor_document_id),
    )
    return _document_response(view)


def _application_response(view: ApplicationView) -> ApplicationResponse:
    """Copy an application view into the public response."""
    return ApplicationResponse(
        vendor_id=view.vendor_id,
        vendor_country_id=view.vendor_country_id,
        country_id=view.country_id,
        legal_name=view.legal_name,
        display_name=view.display_name,
        vendor_type=view.vendor_type,
        vendor_status=view.vendor_status,
        onboarding_status=view.onboarding_status,
        selling_enabled=view.selling_enabled,
        payout_enabled=view.payout_enabled,
        version=view.version,
        documents=[_document_response(document) for document in view.documents],
    )


def _document_response(view: DocumentView) -> DocumentResponse:
    """Copy document metadata and omit the storage key."""
    return DocumentResponse(
        vendor_document_id=view.vendor_document_id,
        document_type=view.document_type,
        verification_status=view.verification_status,
    )
