"""Build request-scoped seller services."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.core.transactions import DatabaseTransaction
from app.modules.access.application import AccessApplicationBuilder
from app.modules.access.dependencies import get_access_application
from app.modules.access.vendor_grants import (
    AssignmentVendorAuthorizer,
    SessionVendorRoleGranter,
)
from app.modules.identity.grants import AuthGrantError
from app.modules.identity.repository import AccountRepository
from app.modules.identity.types import GrantPurpose
from app.modules.vendor.applications import VendorApplicationService
from app.modules.vendor.errors import VendorServiceError
from app.modules.vendor.invitations import VendorInvitationService
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.reviews import VendorReviewService
from app.ports.document_storage import MemoryDocumentStorage


def commit_vendor(db: Session, action):
    """Run one seller mutation and commit only after it succeeds."""
    try:
        result = action()
        db.commit()
        return result
    except VendorServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except AuthGrantError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail="Step-up grant is unavailable.") from exc
    except Exception:
        db.rollback()
        raise


def get_application_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> VendorApplicationService:
    """Build the seller application service for one request."""
    builder = _builder(request)
    return VendorApplicationService(
        VendorRepository(db),
        AccountRepository(db),
        builder.countries,
        _storage(request),
        AssignmentVendorAuthorizer(db, builder.clock),
        builder.clock,
    )


def get_review_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    access: Annotated[object, Depends(get_access_application)],
) -> VendorReviewService:
    """Build the seller review service for one request."""
    builder = _builder(request)
    grants = access.assignments.grants
    def consume(token: str, actor: AuthContext, action_class: str) -> None:
        """Consume a session-bound step-up grant."""
        grants.consume(
            token,
            user_id=actor.user_id,
            session_id=actor.session_id,
            purpose=GrantPurpose.STEP_UP,
            action_class=action_class,
            subject_hash=None,
        )

    return VendorReviewService(
        VendorRepository(db),
        AccountRepository(db),
        builder.countries,
        AssignmentVendorAuthorizer(db, builder.clock),
        SessionVendorRoleGranter(db),
        builder.audit_writer,
        DatabaseTransaction(db),
        builder.clock,
        consume,
    )


def get_invitation_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> VendorInvitationService:
    """Build the staff invitation service for one request."""
    builder = _builder(request)
    return VendorInvitationService(
        VendorRepository(db),
        AccountRepository(db),
        builder.countries,
        AssignmentVendorAuthorizer(db, builder.clock),
        SessionVendorRoleGranter(db),
        builder.clock,
    )


def _builder(request: Request) -> AccessApplicationBuilder:
    """Return the process access builder that owns shared adapters."""
    builder = getattr(request.app.state, "access_application_factory", None)
    if not isinstance(builder, AccessApplicationBuilder):
        raise HTTPException(status_code=503, detail="Vendor services are unavailable.")
    return builder


def _storage(request: Request) -> MemoryDocumentStorage:
    """Reuse one in-process document store for the application lifetime."""
    storage = getattr(request.app.state, "vendor_document_storage", None)
    if not isinstance(storage, MemoryDocumentStorage):
        storage = MemoryDocumentStorage()
        request.app.state.vendor_document_storage = storage
    return storage
