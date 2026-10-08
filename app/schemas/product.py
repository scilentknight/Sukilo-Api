"""Pydantic schemas for product APIs."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductImageRead(BaseModel):
    """Product image returned to API clients."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    image_url: str
    is_primary: bool
    created_at: datetime


class ProductRead(BaseModel):
    """Product with related images."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    category: str
    brand: str | None
    price: Decimal
    currency: str
    source: str
    gtin: str | None = None
    created_at: datetime
    updated_at: datetime
    images: list[ProductImageRead] = Field(default_factory=list)


class ProductListResponse(BaseModel):
    """Paginated product list."""

    items: list[ProductRead]
    skip: int
    limit: int
    total: int


class CategoryRead(BaseModel):
    """Category label with catalog size."""

    name: str
    count: int


class CategoryListResponse(BaseModel):
    """All marketplace categories."""

    items: list[CategoryRead] = Field(default_factory=list)
