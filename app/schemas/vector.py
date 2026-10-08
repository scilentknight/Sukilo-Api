"""Schemas for vector indexing and search APIs."""

from typing import Any

from pydantic import BaseModel, Field


class VectorHealthResponse(BaseModel):
    """Qdrant connectivity status."""

    qdrant: str
    collection: str
    embedding_model: str


class VectorIndexResponse(BaseModel):
    """Response after indexing one image vector."""

    point_id: str
    vector_size: int
    collection: str


class VectorSearchHit(BaseModel):
    """One similarity search result."""

    point_id: str
    score: float
    payload: dict[str, Any] = Field(default_factory=dict)


class VectorSearchResponse(BaseModel):
    """Ranked neighbors for a query image."""

    hits: list[VectorSearchHit]


class VectorSmokeResponse(BaseModel):
    """End-to-end index and search verification."""

    indexed_point_id: str
    top_hit_point_id: str
    top_score: float
    nearest_neighbor_match: bool
