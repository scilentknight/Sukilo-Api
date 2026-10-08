"""Unit tests for product API response schemas."""

from app.schemas.product import ProductListResponse


def test_product_list_response_includes_total() -> None:
    """Include the filtered catalog size in paginated responses."""
    response = ProductListResponse(
        items=[],
        skip=0,
        limit=24,
        total=100,
    )
    assert response.model_dump()["total"] == 100
