"""Customer visual search endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import (
    get_embedding_service,
    get_product_service,
    get_vector_service,
)
from app.schemas.product import ProductRead
from app.schemas.search import ImageSearchResponse
from app.services.embedding_service import EmbeddingService
from app.services.product_service import ProductService
from app.services.search_service import (
    DEFAULT_PRODUCT_LIMIT,
    MAX_PRODUCT_LIMIT,
    SearchService,
)
from app.services.vector_service import VectorService
from app.utils.image_files import read_rgb_image

router = APIRouter(prefix="/search", tags=["Search"])


def _search_service(db: Session = Depends(get_db)) -> SearchService:
    """Build a search service for the current request.

    Args:
        db: Request-scoped database session.

    Returns:
        Configured search service instance.
    """
    return SearchService(db=db)


@router.get("/barcode", response_model=ProductRead)
def search_product_by_barcode(
    code: str,
    service: ProductService = Depends(get_product_service),
) -> ProductRead:
    """Resolve a GS1 Digital Link or raw barcode to one product."""
    product = service.get_product_by_scanned_code(code)
    return ProductRead.model_validate(product)


@router.post("/image", response_model=ImageSearchResponse)
def search_products_by_image(
    image: UploadFile = File(...),
    limit: int = Form(DEFAULT_PRODUCT_LIMIT),
    category: str | None = Form(None),
    service: SearchService = Depends(_search_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_service: VectorService = Depends(get_vector_service),
) -> ImageSearchResponse:
    """Return catalog products visually similar to an uploaded image."""
    if limit < 1 or limit > MAX_PRODUCT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"limit must be between 1 and {MAX_PRODUCT_LIMIT}.",
        )
    max_upload_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    pil_image = read_rgb_image(image, max_upload_bytes)
    normalized_category = category.strip() if category else None
    if normalized_category == "":
        normalized_category = None
    return service.search_by_image(
        image=pil_image,
        limit=limit,
        category=normalized_category,
        min_similarity=settings.SEARCH_MIN_SIMILARITY,
        embedding_service=embedding_service,
        vector_service=vector_service,
    )
