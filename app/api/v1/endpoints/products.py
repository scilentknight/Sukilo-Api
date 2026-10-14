"""Product catalog endpoints."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.dependencies import (
    get_embedding_service,
    get_product_service,
    get_vector_service,
)
from app.schemas.product import (
    CategoryListResponse,
    CategoryRead,
    ProductListResponse,
    ProductRead,
)
from app.services.embedding_service import EmbeddingService
from app.services.product_service import ProductService
from app.services.vector_service import VectorService

router = APIRouter(prefix="/products", tags=["Products"])


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    name: str = Form(...),
    category: str = Form(...),
    price: Decimal = Form(...),
    currency: str = Form(...),
    brand: str | None = Form(None),
    description: str | None = Form(None),
    gtin: str | None = Form(None),
    images: list[UploadFile] = File(...),
    service: ProductService = Depends(get_product_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_service: VectorService = Depends(get_vector_service),
) -> ProductRead:
    """Create a vendor product, store images, and index vectors in Qdrant."""
    product = service.create_vendor_product(
        name=name,
        description=description,
        category=category,
        brand=brand,
        price=price,
        currency=currency,
        images=images,
        embedding_service=embedding_service,
        vector_service=vector_service,
        gtin=gtin,
    )
    return ProductRead.model_validate(product)


@router.get("/categories", response_model=CategoryListResponse)
def list_categories(
    service: ProductService = Depends(get_product_service),
) -> CategoryListResponse:
    """List distinct product categories for marketplace navigation."""
    rows = service.list_categories()
    return CategoryListResponse(
        items=[CategoryRead(name=name, count=count) for name, count in rows],
    )


@router.get("", response_model=ProductListResponse)
def list_products(
    skip: int = 0,
    limit: int = 50,
    category: str | None = None,
    q: str | None = None,
    service: ProductService = Depends(get_product_service),
) -> ProductListResponse:
    """List products newest first with optional category and text filters."""
    if skip < 0 or limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="skip must be >= 0 and limit must be between 1 and 100.",
        )
    items = service.list_products(
        skip=skip,
        limit=limit,
        category=category,
        search=q,
    )
    total = service.count_products(category=category, search=q)
    return ProductListResponse(
        items=[ProductRead.model_validate(item) for item in items],
        skip=skip,
        limit=limit,
        total=total,
    )


@router.get("/gtin/{gtin}", response_model=ProductRead)
def get_product_by_gtin(
    gtin: str,
    service: ProductService = Depends(get_product_service),
) -> ProductRead:
    """Return one product by GS1 GTIN-14."""
    product = service.get_product_by_gtin(gtin)
    return ProductRead.model_validate(product)


@router.get("/{product_id}", response_model=ProductRead)
def get_product(
    product_id: int,
    service: ProductService = Depends(get_product_service),
) -> ProductRead:
    """Return one product with images."""
    product = service.get_product(product_id)
    return ProductRead.model_validate(product)
