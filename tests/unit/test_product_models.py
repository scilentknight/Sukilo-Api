"""Unit tests for product SQLAlchemy metadata."""

from app.core.database import Base
from app.models import Product, ProductImage


def test_product_table_columns() -> None:
    table = Product.__table__
    column_names = {column.name for column in table.columns}
    assert column_names == {
        "id",
        "name",
        "description",
        "category",
        "brand",
        "price",
        "currency",
        "source",
        "gtin",
        "created_at",
        "updated_at",
    }


def test_product_image_table_columns() -> None:
    table = ProductImage.__table__
    column_names = {column.name for column in table.columns}
    assert column_names == {
        "id",
        "product_id",
        "image_url",
        "is_primary",
        "created_at",
    }


def test_product_tables_registered_in_metadata() -> None:
    assert "products" in Base.metadata.tables
    assert "product_images" in Base.metadata.tables
