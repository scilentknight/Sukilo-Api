"""Pydantic schemas for customer image search."""

from decimal import Decimal

from pydantic import BaseModel, Field


class ImageSearchResult(BaseModel):
    """One visually similar product returned to clients."""

    product_id: int
    name: str
    brand: str | None
    category: str
    price: Decimal
    currency: str
    image_url: str
    similarity: float = Field(ge=0.0, le=1.0)


class ImageSearchResponse(BaseModel):
    """Ranked similar products for a query image."""

    results: list[ImageSearchResult] = Field(default_factory=list)
