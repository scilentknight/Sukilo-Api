"""FastAPI dependencies for shared application services."""

from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services.embedding_service import EmbeddingService
from app.services.image_service import ImageService
from app.services.product_service import ProductService
from app.services.vector_service import VectorService


def get_embedding_service(request: Request) -> EmbeddingService | None:
    """Return the embedding service stored on application state.

    Args:
        request: Current HTTP request.

    Returns:
        Loaded embedding service instance, or None if disabled.

    Raises:
        HTTPException: If the vector stack was required but not initialized.
    """
    service = getattr(request.app.state, "embedding_service", None)
    if service is None and not settings.ENABLE_VECTOR_STACK:
        return None
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding service is not available.",
        )
    return service


def get_vector_service(request: Request) -> VectorService | None:
    """Return the vector service stored on application state.

    Args:
        request: Current HTTP request.

    Returns:
        Connected vector service instance, or None if disabled.

    Raises:
        HTTPException: If the vector stack was required but not initialized.
    """
    service = getattr(request.app.state, "vector_service", None)
    if service is None and not settings.ENABLE_VECTOR_STACK:
        return None
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector service is not available.",
        )
    return service


def get_product_service(db: Session = Depends(get_db)) -> ProductService:
    """Build a product service for the current request.

    Args:
        db: Request-scoped database session.

    Returns:
        Configured product service instance.
    """
    image_service = ImageService(upload_dir=Path(settings.UPLOAD_DIR))
    image_service.ensure_upload_root()
    return ProductService(
        db=db,
        image_service=image_service,
        max_upload_bytes=settings.MAX_UPLOAD_MB * 1024 * 1024,
    )
