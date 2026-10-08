"""Unit tests for default dataset path resolution."""

from pathlib import Path

import pytest

from app.core.dataset_paths import (
    FASHION_KAGGLE_SLUG,
    resolve_fashion_product_images_dataset,
)


def test_uses_full_fashion_product_images_dataset() -> None:
    """Use the full Kaggle fashion dataset slug."""
    assert FASHION_KAGGLE_SLUG == "paramaggarwal/fashion-product-images-dataset"


@pytest.mark.parametrize(
    "relative_root",
    (Path(), Path("fashion-dataset"), Path("fashion-dataset/fashion-dataset")),
)
def test_resolve_supported_fashion_dataset_layouts(
    tmp_path: Path,
    relative_root: Path,
) -> None:
    """Resolve metadata and images from supported archive layouts."""
    dataset_dir = tmp_path / relative_root
    dataset_dir.mkdir(parents=True, exist_ok=True)
    csv_path = dataset_dir / "styles.csv"
    images_dir = dataset_dir / "images"
    csv_path.write_text("id,productDisplayName\n", encoding="utf-8")
    images_dir.mkdir()
    assert resolve_fashion_product_images_dataset(tmp_path) == (
        csv_path,
        images_dir,
    )
