"""Phase 1 vector indexing and search endpoints."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.dependencies import get_embedding_service, get_vector_service
from app.schemas.vector import (
    VectorHealthResponse,
    VectorIndexResponse,
    VectorSearchHit,
    VectorSearchResponse,
    VectorSmokeResponse,
)
from app.services.embedding_service import EmbeddingService
from app.services.vector_service import VectorService

router = APIRouter(prefix="/vectors", tags=["Vectors"])


def _load_rgb_image(upload: UploadFile) -> Image.Image:
    """Read an uploaded file into a PIL RGB image.

    Args:
        upload: Multipart image upload.

    Returns:
        Decoded RGB image.

    Raises:
        HTTPException: If the file is missing or not a valid image.
    """
    if not upload.content_type or not upload.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload must be an image file.",
        )
    raw_bytes = upload.file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image is empty.",
        )
    try:
        return Image.open(io.BytesIO(raw_bytes))
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to decode image.",
        ) from exc


@router.get("/health", response_model=VectorHealthResponse)
def vector_health(
    vector_service: VectorService = Depends(get_vector_service),
) -> VectorHealthResponse:
    """Report Qdrant and embedding configuration health."""
    qdrant_status = "connected" if vector_service.ping() else "disconnected"
    return VectorHealthResponse(
        qdrant=qdrant_status,
        collection=vector_service.collection_name,
        embedding_model=settings.DINOV2_MODEL,
    )


@router.post("/index", response_model=VectorIndexResponse)
def index_image(
    image: UploadFile = File(...),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_service: VectorService = Depends(get_vector_service),
) -> VectorIndexResponse:
    """Embed an image and store its vector in Qdrant."""
    pil_image = _load_rgb_image(image)
    vector = embedding_service.embed(pil_image)
    point_id = vector_service.upsert_point(
        vector=vector,
        payload={"source": "phase1_demo"},
    )
    return VectorIndexResponse(
        point_id=point_id,
        vector_size=len(vector),
        collection=vector_service.collection_name,
    )


@router.post("/search", response_model=VectorSearchResponse)
def search_image(
    image: UploadFile = File(...),
    limit: int = 5,
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_service: VectorService = Depends(get_vector_service),
) -> VectorSearchResponse:
    """Search Qdrant for vectors similar to the uploaded image."""
    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limit must be between 1 and 50.",
        )
    pil_image = _load_rgb_image(image)
    vector = embedding_service.embed(pil_image)
    hits = vector_service.search(vector=vector, limit=limit)
    return VectorSearchResponse(
        hits=[VectorSearchHit(**hit) for hit in hits],
    )


@router.post("/smoke", response_model=VectorSmokeResponse)
def smoke_test(
    image: UploadFile = File(...),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    vector_service: VectorService = Depends(get_vector_service),
) -> VectorSmokeResponse:
    """Index one image and confirm it is the top nearest neighbor."""
    pil_image = _load_rgb_image(image)
    vector = embedding_service.embed(pil_image)
    point_id = vector_service.upsert_point(
        vector=vector,
        payload={"source": "phase1_smoke"},
    )
    hits = vector_service.search(vector=vector, limit=1)
    if not hits:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Qdrant returned no search results.",
        )
    top_hit = hits[0]
    return VectorSmokeResponse(
        indexed_point_id=point_id,
        top_hit_point_id=top_hit["point_id"],
        top_score=top_hit["score"],
        nearest_neighbor_match=top_hit["point_id"] == point_id,
    )
