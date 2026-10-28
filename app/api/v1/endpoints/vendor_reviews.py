"""Expose country seller review routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.vendor_applications import _application_response
from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated, require_recent_step_up
from app.modules.vendor.dependencies import commit_vendor, get_review_service
from app.modules.vendor.reviews import VendorReviewService
from app.modules.vendor.schemas import ApplicationResponse, ReviewReasonRequest

router = APIRouter(prefix="/vendor", tags=["Vendor review"])


@router.get("/reviews", response_model=list[ApplicationResponse])
def list_reviews(
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorReviewService, Depends(get_review_service)],
) -> list[ApplicationResponse]:
    """List applications waiting in one country."""
    views = commit_vendor(
        db,
        lambda: service.list_for_country_code(context, country_code),
    )
    return [_application_response(view) for view in views]


@router.post("/reviews/{vendor_country_id}/approve", response_model=ApplicationResponse)
def approve_application(
    vendor_country_id: UUID,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorReviewService, Depends(get_review_service)],
) -> ApplicationResponse:
    """Approve one application and grant the owner badge."""
    view = commit_vendor(
        db,
        lambda: service.approve(
            context,
            vendor_country_id,
            step_up_token=step_up_token,
        ),
    )
    return _application_response(view)


@router.post("/reviews/{vendor_country_id}/reject", response_model=ApplicationResponse)
def reject_application(
    vendor_country_id: UUID,
    payload: ReviewReasonRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorReviewService, Depends(get_review_service)],
) -> ApplicationResponse:
    """Reject one application without granting a badge."""
    view = commit_vendor(
        db,
        lambda: service.reject(
            context,
            vendor_country_id,
            reason=payload.reason,
            step_up_token=step_up_token,
        ),
    )
    return _application_response(view)


@router.post(
    "/reviews/{vendor_country_id}/request-info",
    response_model=ApplicationResponse,
)
def request_application_info(
    vendor_country_id: UUID,
    payload: ReviewReasonRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorReviewService, Depends(get_review_service)],
) -> ApplicationResponse:
    """Return an application to the applicant for more information."""
    view = commit_vendor(
        db,
        lambda: service.request_info(
            context,
            vendor_country_id,
            reason=payload.reason,
            step_up_token=step_up_token,
        ),
    )
    return _application_response(view)
