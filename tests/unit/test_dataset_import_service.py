"""Unit tests for Kaggle CSV row parsing."""

from decimal import Decimal
from pathlib import Path

from app.services.dataset_import_service import _parse_dataset_row, _parse_price


def test_parse_dataset_row_maps_common_fashion_headers(tmp_path: Path) -> None:
    image_path = tmp_path / "123.jpg"
    image_path.write_bytes(b"fake")
    row = {
        "productDisplayName": "Blue Shoe",
        "masterCategory": "Footwear",
        "brand": "Demo",
        "price": "29.99",
        "filename": "123.jpg",
        "gender": "Men",
    }
    parsed = _parse_dataset_row(row, tmp_path, "USD")
    assert parsed is not None
    assert parsed.name == "Blue Shoe"
    assert parsed.category == "Footwear"
    assert parsed.brand == "Demo"
    assert parsed.price == Decimal("29.99")
    assert parsed.image_path == image_path
    assert "Men" in (parsed.description or "")


def test_parse_dataset_row_resolves_myntra_id_image(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    image_path = images_dir / "15970.jpg"
    image_path.write_bytes(b"fake")
    row = {
        "id": "15970",
        "productDisplayName": "Turtle Check Men Navy Blue Shirt",
        "masterCategory": "Apparel",
        "gender": "Men",
    }
    parsed = _parse_dataset_row(row, images_dir, "USD")
    assert parsed is not None
    assert parsed.image_path == image_path


def test_parse_dataset_row_returns_none_without_image(tmp_path: Path) -> None:
    row = {"name": "Hat", "category": "Accessories"}
    assert _parse_dataset_row(row, tmp_path, "USD") is None


def test_parse_price_defaults_when_invalid() -> None:
    assert _parse_price(None) == Decimal("1.00")
    assert _parse_price("not-a-price") == Decimal("1.00")
