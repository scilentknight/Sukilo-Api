"""Expose shared product and seller offer routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated, require_recent_step_up
from app.modules.catalog.dependencies import commit_catalog, get_offer_service
from app.modules.catalog.offers import (
    AttributeInput,
    CatalogOfferService,
    MediaView,
    OfferView,
    ProductView,
    PublicOfferView,
)
from app.modules.catalog.schemas import (
    MediaCreateRequest,
    MediaResponse,
    OfferCreateRequest,
    OfferPublishRequest,
    OfferResponse,
    ProductCreateRequest,
    ProductModerateRequest,
    ProductResponse,
    ProductSubmitRequest,
    PublicOfferResponse,
    SkuCreateRequest,
    SkuResponse,
)

router = APIRouter(prefix="/catalog", tags=["Catalog"])


@router.post("/products", response_model=ProductResponse, status_code=201)
def create_product(
    payload: ProductCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> ProductResponse:
    """Create a draft shared product."""
    view = commit_catalog(
        db,
        lambda: service.create_product(
            context,
            payload.vendor_id,
            payload.country_code,
            payload.category_id,
            payload.brand_id,
            payload.locale_code,
            payload.name,
            payload.description,
            _attributes(payload.attributes),
        ),
    )
    return _product_response(view)


@router.post("/products/{product_id}/skus", response_model=SkuResponse, status_code=201)
def add_sku(
    product_id: UUID,
    payload: SkuCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> SkuResponse:
    """Add one variant to a shared product."""
    view = commit_catalog(
        db,
        lambda: service.add_sku(
            context,
            product_id,
            payload.vendor_id,
            payload.country_code,
            payload.sku_code,
            _attributes(payload.attributes),
        ),
    )
    return SkuResponse(sku_id=view.sku_id, product_id=view.product_id, sku_code=view.sku_code)


@router.post("/products/{product_id}/media", response_model=MediaResponse, status_code=201)
def attach_media(
    product_id: UUID,
    payload: MediaCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> MediaResponse:
    """Attach private media to a product."""
    view = commit_catalog(
        db,
        lambda: service.attach_media(
            context,
            product_id,
            payload.vendor_id,
            payload.country_code,
            payload.content_base64,
            payload.media_type,
            payload.sort_order,
            payload.is_primary,
        ),
    )
    return _media_response(view)


@router.post("/products/{product_id}/submit", response_model=ProductResponse)
def submit_product(
    product_id: UUID,
    payload: ProductSubmitRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> ProductResponse:
    """Submit a product for review in the named countries."""
    view = commit_catalog(
        db,
        lambda: service.submit_product(
            context,
            product_id,
            payload.vendor_id,
            payload.country_codes,
        ),
    )
    return _product_response(view)


@router.post("/products/{product_id}/moderate", response_model=ProductResponse)
def moderate_product(
    product_id: UUID,
    payload: ProductModerateRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> ProductResponse:
    """Moderate one product country."""
    view = commit_catalog(
        db,
        lambda: service.moderate_product(
            context,
            product_id,
            payload.country_code,
            payload.decision,
            step_up_token,
        ),
    )
    return _product_response(view)


@router.post("/offers", response_model=OfferResponse, status_code=201)
def create_offer(
    payload: OfferCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> OfferResponse:
    """Create a draft seller offer."""
    view = commit_catalog(
        db,
        lambda: service.create_offer(
            context,
            payload.vendor_id,
            payload.store_id,
            payload.sku_id,
            payload.country_code,
            payload.seller_sku,
            payload.list_price,
            payload.currency_code,
        ),
    )
    return _offer_response(view)


@router.post("/offers/{offer_id}/publish", response_model=OfferResponse)
def publish_offer(
    offer_id: UUID,
    payload: OfferPublishRequest,
    step_up_token: Annotated[str, Depends(require_recent_step_up)],
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> OfferResponse:
    """Publish one offer in one country."""
    view = commit_catalog(
        db,
        lambda: service.publish_offer(context, offer_id, payload.country_code, step_up_token),
    )
    return _offer_response(view)


@router.get("/offers", response_model=list[PublicOfferResponse])
def list_offers(
    country_code: Annotated[str, Query(min_length=2, max_length=2)],
    locale_code: Annotated[str, Query(min_length=2, max_length=16)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[CatalogOfferService, Depends(get_offer_service)],
) -> list[PublicOfferResponse]:
    """List published offers for one country."""
    views = commit_catalog(db, lambda: service.list_offers(country_code, locale_code))
    return [_public_response(view) for view in views]


def _attributes(values) -> list[AttributeInput]:
    """Map request values onto service inputs."""
    return [
        AttributeInput(
            attribute_id=value.attribute_id,
            option_id=value.option_id,
            value_text=value.value_text,
            value_number=value.value_number,
        )
        for value in values
    ]


def _product_response(view: ProductView) -> ProductResponse:
    """Map a product view onto the HTTP response."""
    return ProductResponse(product_id=view.product_id, status=view.status)


def _media_response(view: MediaView) -> MediaResponse:
    """Map a media view onto the HTTP response."""
    return MediaResponse(
        media_id=view.media_id,
        storage_key=view.storage_key,
        media_type=view.media_type,
        sort_order=view.sort_order,
        is_primary=view.is_primary,
    )


def _offer_response(view: OfferView) -> OfferResponse:
    """Map an offer view onto the HTTP response."""
    return OfferResponse(
        offer_id=view.offer_id,
        vendor_id=view.vendor_id,
        store_id=view.store_id,
        sku_id=view.sku_id,
        status=view.status,
        publication_status=view.publication_status,
        currency_code=view.currency_code,
        list_price=view.list_price,
    )


def _public_response(view: PublicOfferView) -> PublicOfferResponse:
    """Map a public offer view onto the HTTP response."""
    return PublicOfferResponse(
        offer_id=view.offer_id,
        store_id=view.store_id,
        sku_id=view.sku_id,
        product_id=view.product_id,
        currency_code=view.currency_code,
        list_price=view.list_price,
        name=view.name,
    )
