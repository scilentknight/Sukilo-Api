"""Expose vendor staff invitation routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated
from app.modules.vendor.dependencies import commit_vendor, get_invitation_service
from app.modules.vendor.invitations import InvitationView, VendorInvitationService
from app.modules.vendor.schemas import (
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationResponse,
    MemberRevokeRequest,
)

router = APIRouter(prefix="/vendor/staff", tags=["Vendor staff"])


@router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    payload: InvitationCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorInvitationService, Depends(get_invitation_service)],
) -> InvitationResponse:
    """Invite staff without revealing whether the email is registered."""
    view = commit_vendor(
        db,
        lambda: service.invite_by_code(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
            email=payload.email,
        ),
    )
    return _invitation_response(view)


@router.post("/invitations/{invitation_id}/accept", status_code=status.HTTP_204_NO_CONTENT)
def accept_invitation(
    invitation_id: UUID,
    payload: InvitationAcceptRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorInvitationService, Depends(get_invitation_service)],
) -> None:
    """Accept one invitation and grant staff access."""
    commit_vendor(
        db,
        lambda: service.accept(context, invitation_id, token=payload.token),
    )


@router.post("/members/{vendor_member_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_member(
    vendor_member_id: UUID,
    payload: MemberRevokeRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorInvitationService, Depends(get_invitation_service)],
) -> None:
    """Suspend a staff member and revoke that country badge."""
    commit_vendor(
        db,
        lambda: service.revoke_by_code(
            context,
            vendor_member_id,
            country_code=payload.country_code,
            reason=payload.reason,
        ),
    )


def _invitation_response(view: InvitationView) -> InvitationResponse:
    """Copy the one-time invitation token into the response."""
    return InvitationResponse(
        invitation_id=view.invitation_id,
        expires_at=view.expires_at,
        token=view.token,
    )
