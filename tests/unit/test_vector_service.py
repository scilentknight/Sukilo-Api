"""Unit tests for Qdrant payload helpers."""

from unittest.mock import MagicMock

from app.services.vector_service import VectorService


def test_upsert_product_image_builds_payload() -> None:
    service = VectorService(url="http://localhost:6333", collection_name="product_images")
    service._client = MagicMock()
    point_id = service.upsert_product_image(
        vector=[0.1, 0.2],
        product_id=10,
        image_id=20,
        category="shoes",
        brand="Demo Brand",
        source="vendor",
    )
    assert point_id == "20"
    service._client.upsert.assert_called_once()
    points = service._client.upsert.call_args.kwargs["points"]
    assert points[0].id == 20
    assert points[0].payload == {
        "product_id": 10,
        "image_id": 20,
        "category": "shoes",
        "brand": "Demo Brand",
        "source": "vendor",
    }
