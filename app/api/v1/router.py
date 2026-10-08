from fastapi import APIRouter

from app.api.v1.endpoints import health, products, search, vectors

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(products.router)
api_router.include_router(search.router)
api_router.include_router(vectors.router)