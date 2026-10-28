"""Expose shared taxonomy routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated, require_recent_step_up
from app.modules.catalog.dependencies import commit_catalog, get_catalog_service
from app.modules.catalog.schemas import (
    BrandDecisionRequest,
    BrandRequestCreate,
    BrandRequestResponse,
    BrandResponse,
    CategoryAttributesRequest,
    CategoryCreateRequest,
    CategoryDecisionRequest,
    CategoryRequestCreate,
    CategoryRequestResponse,
    CategoryResponse,
    CountryEnableRequest,
)
from app.modules.catalog.service import (
    AttributeLink,
    BrandRequestView,
    BrandView,
    CatalogTaxonomyService,
    CategoryRequestView,
    CategoryView,
)

router = APIRouter(prefix="/catalog", tags=["Catalog"])


@router.get("/categories", response_model=list[CategoryResponse])
def list_categories(
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
    locale_code: Annotated[str, Query(min_length=2, max_length=16)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> list[CategoryResponse]:
    """List categories enabled in one country."""
    views = commit_catalog(db, lambda: service.list_categories(country_code, locale_code))
    return [_category_response(view) for view in views]


@router.post("/categories", response_model=CategoryResponse, status_code=201)
def create_category(
    payload: CategoryCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> CategoryResponse:
    """Create a canonical category that starts disabled in every country."""
    view = commit_catalog(
        db,
        lambda: service.create_category(
            context,
            code=payload.code,
            parent_id=payload.parent_id,
            locale_code=payload.locale_code,
            name=payload.name,
        ),
    )
    return _category_response(view)


@router.post("/categories/{category_id}/enable", status_code=204)
def enable_category(
    category_id: UUID,
    payload: CountryEnableRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> None:
    """Enable one category in the reviewer's country."""
    commit_catalog(
        db,
        lambda: service.enable_category(
            context,
            category_id,
            country_code=payload.country_code,
            step_up_token=step_up_token,
        ),
    )


@router.post("/category-requests", response_model=CategoryRequestResponse, status_code=201)
def request_category(
    payload: CategoryRequestCreate,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> CategoryRequestResponse:
    """Propose a category change without publishing it."""
    view = commit_catalog(
        db,
        lambda: service.request_category(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
            proposed_code=payload.proposed_code,
            proposed_parent_id=payload.proposed_parent_id,
            category_id=payload.category_id,
        ),
    )
    return _category_request_response(view)


@router.post("/category-requests/{request_id}/decide", response_model=CategoryRequestResponse)
def decide_category(
    request_id: UUID,
    payload: CategoryDecisionRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> CategoryRequestResponse:
    """Approve or reject a category proposal."""
    view = commit_catalog(
        db,
        lambda: service.decide_category(
            context,
            request_id,
            decision=payload.decision,
            step_up_token=step_up_token,
        ),
    )
    return _category_request_response(view)


@router.get("/brands", response_model=list[BrandResponse])
def list_brands(
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> list[BrandResponse]:
    """List brands enabled in one country."""
    views = commit_catalog(db, lambda: service.list_brands(country_code))
    return [_brand_response(view) for view in views]


@router.post("/brand-requests", response_model=BrandRequestResponse, status_code=201)
def request_brand(
    payload: BrandRequestCreate,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> BrandRequestResponse:
    """Request a brand without creating it."""
    view = commit_catalog(
        db,
        lambda: service.request_brand(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
            proposed_name=payload.proposed_name,
        ),
    )
    return _brand_request_response(view)


@router.post("/brand-requests/{request_id}/decide", response_model=BrandRequestResponse)
def decide_brand(
    request_id: UUID,
    payload: BrandDecisionRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> BrandRequestResponse:
    """Link or create a brand for one country."""
    view = commit_catalog(
        db,
        lambda: service.decide_brand(
            context,
            request_id,
            decision=payload.decision,
            link_brand_id=payload.link_brand_id,
            step_up_token=step_up_token,
        ),
    )
    return _brand_request_response(view)


@router.put("/categories/{category_id}/attributes", status_code=204)
def replace_attributes(
    category_id: UUID,
    payload: CategoryAttributesRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogTaxonomyService, Depends(get_catalog_service)],
) -> None:
    """Replace the attribute set for one category."""
    links = [
        AttributeLink(item.attribute_id, item.required) for item in payload.attributes
    ]
    commit_catalog(db, lambda: service.replace_attributes(context, category_id, links))


def _category_response(view: CategoryView) -> CategoryResponse:
    """Map a category view to the HTTP response."""
    return CategoryResponse(
        category_id=view.category_id,
        parent_id=view.parent_id,
        code=view.code,
        name=view.name,
    )


def _category_request_response(view: CategoryRequestView) -> CategoryRequestResponse:
    """Map a category request view to the HTTP response."""
    return CategoryRequestResponse(
        request_id=view.request_id,
        status=view.status,
        category_id=view.category_id,
    )


def _brand_response(view: BrandView) -> BrandResponse:
    """Map a brand view to the HTTP response."""
    return BrandResponse(brand_id=view.brand_id, name=view.name)


def _brand_request_response(view: BrandRequestView) -> BrandRequestResponse:
    """Map a brand request view to the HTTP response."""
    return BrandRequestResponse(
        request_id=view.request_id,
        status=view.status,
        brand_id=view.brand_id,
    )
