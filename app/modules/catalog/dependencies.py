"""Build request-scoped catalog taxonomy services."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.application import AccessApplicationBuilder
from app.modules.access.dependencies import get_access_application
from app.modules.access.vendor_grants import AssignmentVendorAuthorizer
from app.modules.catalog.errors import CatalogServiceError
from app.modules.catalog.repository import CatalogRepository
from app.modules.catalog.service import CatalogTaxonomyService
from app.modules.identity.grants import AuthGrantError
from app.modules.identity.types import GrantPurpose
from app.modules.vendor.repository import VendorRepository
from app.modules.vendor.types import OnboardingStatus


def commit_catalog(db: Session, action):
    """Run one taxonomy mutation and commit only after it succeeds."""
    try:
        result = action()
        db.commit()
        return result
    except CatalogServiceError as exc:
        db.rollback()
        detail: str | dict[str, str] = str(exc)
        if exc.code is not None:
            detail = {"code": exc.code, "message": str(exc)}
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    except AuthGrantError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail="Step-up grant is unavailable.") from exc
    except Exception:
        db.rollback()
        raise


class VendorApprovedSellerLookup:
    """Read seller approval from the vendor country table."""

    def __init__(self, db: Session) -> None:
        """Initialize the lookup.

        Args:
            db: Active SQLAlchemy session.
        """
        self.vendors = VendorRepository(db)

    def is_approved(self, vendor_id, country_id) -> bool:
        """Return whether the vendor country application is approved."""
        country = self.vendors.get_country_for_market(vendor_id, country_id)
        return country is not None and country.onboarding_status is OnboardingStatus.APPROVED


def get_catalog_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    access: Annotated[object, Depends(get_access_application)],
) -> CatalogTaxonomyService:
    """Build the taxonomy service for one request."""
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
    return CatalogTaxonomyService(
        CatalogRepository(db),
        builder.countries,
        AssignmentVendorAuthorizer(db, builder.clock),
        VendorApprovedSellerLookup(db),
        consume,
    )


def _builder(request: Request) -> AccessApplicationBuilder:
    """Return the process access builder that owns shared adapters."""
    builder = getattr(request.app.state, "access_application_factory", None)
    if not isinstance(builder, AccessApplicationBuilder):
        raise HTTPException(status_code=503, detail="Catalog services are unavailable.")
    return builder
