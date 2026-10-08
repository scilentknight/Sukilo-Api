"""FastAPI dependencies for shared application services."""

from fastapi import HTTPException, Request, status

from app.services.embedding_service import EmbeddingService
from app.services.vector_service import VectorService


def get_embedding_service(request: Request) -> EmbeddingService:
    """Return the embedding service stored on application state.

    Args:
        request: Current HTTP request.

    Returns:
        Loaded embedding service instance.

    Raises:
        HTTPException: If the vector stack was not initialized on startup.
    """
    service = getattr(request.app.state, "embedding_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding service is not available.",
        )
    return service


def get_vector_service(request: Request) -> VectorService:
    """Return the vector service stored on application state.

    Args:
        request: Current HTTP request.

    Returns:
        Connected vector service instance.

    Raises:
        HTTPException: If the vector stack was not initialized on startup.
    """
    service = getattr(request.app.state, "vector_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector service is not available.",
        )
    return service
