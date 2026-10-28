from fastapi import APIRouter

from app.api.v1.endpoints import (
    access,
    auth,
    customers,
    health,
    products,
    search,
    vectors,
    vendor_applications,
    vendor_reviews,
    vendor_staff,
    vendor_store,
)

api_router = APIRouter()
api_router.include_router(access.router)
api_router.include_router(auth.router)
api_router.include_router(customers.router)
api_router.include_router(health.router)
api_router.include_router(products.router)
api_router.include_router(search.router)
api_router.include_router(vectors.router)
api_router.include_router(vendor_applications.router)
api_router.include_router(vendor_reviews.router)
api_router.include_router(vendor_staff.router)
api_router.include_router(vendor_store.router)
