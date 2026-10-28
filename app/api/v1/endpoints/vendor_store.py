"""Expose seller store, payout, and warehouse routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.database import get_db
from app.modules.access.dependencies import require_authenticated
from app.modules.vendor.dependencies import commit_vendor, get_storefront_service
from app.modules.vendor.schemas import (
    PayoutAccountRequest,
    PayoutAccountResponse,
    StoreCountryRequest,
    StoreResponse,
    StoreUpsertRequest,
    WarehouseCreateRequest,
    WarehouseResponse,
)
from app.modules.vendor.storefront import (
    PayoutView,
    StoreView,
    VendorStorefrontService,
    WarehouseView,
)

router = APIRouter(prefix="/vendor", tags=["Vendor store"])


@router.put("/stores", response_model=StoreResponse)
def save_store(
    payload: StoreUpsertRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorStorefrontService, Depends(get_storefront_service)],
) -> StoreResponse:
    """Create or update the draft store and one translation."""
    view = commit_vendor(
        db,
        lambda: service.save_store(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
            slug=payload.slug,
            locale_code=payload.locale_code,
            name=payload.name,
            description=payload.description,
            seo_title=payload.seo_title,
            seo_description=payload.seo_description,
        ),
    )
    return _store_response(view)


@router.put("/payout-accounts", response_model=PayoutAccountResponse)
def save_payout_account(
    payload: PayoutAccountRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorStorefrontService, Depends(get_storefront_service)],
) -> PayoutAccountResponse:
    """Add a masked payout destination."""
    view = commit_vendor(
        db,
        lambda: service.save_payout(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
            account_type=payload.account_type,
            currency_code=payload.currency_code,
            account_holder_name=payload.account_holder_name,
            account_number=payload.account_number,
            bank_name=payload.bank_name,
            is_default=payload.is_default,
        ),
    )
    return _payout_response(view)


@router.post("/warehouses", response_model=WarehouseResponse, status_code=201)
def add_warehouse(
    payload: WarehouseCreateRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorStorefrontService, Depends(get_storefront_service)],
) -> WarehouseResponse:
    """Add a warehouse in the owner's country."""
    view = commit_vendor(
        db,
        lambda: service.add_warehouse(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
            code=payload.code,
            address_line_1=payload.address_line_1,
            city_text=payload.city_text,
            postal_code=payload.postal_code,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            fulfillment_enabled=payload.fulfillment_enabled,
            pickup_enabled=payload.pickup_enabled,
            status=payload.status,
        ),
    )
    return _warehouse_response(view)


@router.post("/stores/activate", response_model=StoreResponse)
def activate_store(
    payload: StoreCountryRequest,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorStorefrontService, Depends(get_storefront_service)],
) -> StoreResponse:
    """Run go-live checks and enable selling when they pass."""
    view = commit_vendor(
        db,
        lambda: service.activate(
            context,
            vendor_id=payload.vendor_id,
            country_code=payload.country_code,
        ),
    )
    return _store_response(view)


@router.post("/payout-accounts/{payout_account_id}/verify", response_model=PayoutAccountResponse)
def verify_payout_account(
    payout_account_id: UUID,
    context: Annotated[AuthContext, Depends(require_authenticated)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[VendorStorefrontService, Depends(get_storefront_service)],
) -> PayoutAccountResponse:
    """Verify a payout destination for a country reviewer."""
    view = commit_vendor(
        db,
        lambda: service.verify_payout(context, payout_account_id),
    )
    return _payout_response(view)


def _store_response(view: StoreView) -> StoreResponse:
    """Map a store view to the HTTP response."""
    return StoreResponse(
        store_id=view.store_id,
        vendor_id=view.vendor_id,
        country_id=view.country_id,
        slug=view.slug,
        status=view.status,
        locale_code=view.locale_code,
        name=view.name,
        selling_enabled=view.selling_enabled,
        payout_enabled=view.payout_enabled,
    )


def _payout_response(view: PayoutView) -> PayoutAccountResponse:
    """Map a payout view to the HTTP response."""
    return PayoutAccountResponse(
        vendor_payout_account_id=view.vendor_payout_account_id,
        currency_code=view.currency_code,
        account_number_masked=view.account_number_masked,
        is_default=view.is_default,
        verification_status=view.verification_status,
    )


def _warehouse_response(view: WarehouseView) -> WarehouseResponse:
    """Map a warehouse view to the HTTP response."""
    return WarehouseResponse(
        warehouse_id=view.warehouse_id,
        vendor_id=view.vendor_id,
        country_id=view.country_id,
        code=view.code,
        status=view.status,
        fulfillment_enabled=view.fulfillment_enabled,
    )
