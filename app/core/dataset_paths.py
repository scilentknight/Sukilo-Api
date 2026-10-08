"""Resolve paths for the full Fashion Product Images Kaggle dataset."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
FASHION_KAGGLE_SLUG = "paramaggarwal/fashion-product-images-dataset"
DEFAULT_DATASET_ROOT = REPO_ROOT / "datasets"


def resolve_dataset_root(override: Path | None = None) -> Path:
    """Return the dataset root directory.

    Args:
        override: Optional explicit dataset directory.

    Returns:
        Absolute path to the dataset root folder.
    """
    if override is not None:
        return override.resolve()
    return DEFAULT_DATASET_ROOT.resolve()


def resolve_fashion_product_images_dataset(
    dataset_root: Path | None = None,
) -> tuple[Path, Path]:
    """Locate ``styles.csv`` and the ``images`` folder for the Myntra export.

    Args:
        dataset_root: Dataset directory; defaults to ``<repo>/datasets``.

    Returns:
        Tuple of CSV path and images directory.

    Raises:
        FileNotFoundError: If metadata or images cannot be located.
    """
    root = resolve_dataset_root(dataset_root)
    layout_roots = (
        root,
        root / "fashion-dataset",
        root / "fashion-dataset" / "fashion-dataset",
        root / "myntradataset",
    )
    dataset_dir = next(
        (
            path
            for path in layout_roots
            if (path / "styles.csv").is_file() and (path / "images").is_dir()
        ),
        None,
    )
    if dataset_dir is None:
        raise FileNotFoundError(
            "Could not find styles.csv and images/ under "
            f"{root}. Download the Kaggle dataset first."
        )
    return dataset_dir / "styles.csv", dataset_dir / "images"
