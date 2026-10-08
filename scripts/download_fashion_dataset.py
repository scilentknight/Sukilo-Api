"""Download the full Fashion Product Images dataset into the repo datasets folder."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.dataset_paths import (
    DEFAULT_DATASET_ROOT,
    resolve_fashion_product_images_dataset,
)
from app.services.fashion_dataset_download import (
    download_fashion_product_images_dataset,
)


def main() -> int:
    """Download the dataset and print resolved paths.

    Returns:
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(
        description="Download paramaggarwal/fashion-product-images-dataset from Kaggle.",
    )
    parser.add_argument(
        "--target-dir",
        default=str(DEFAULT_DATASET_ROOT),
        help="Directory to store styles.csv and images/ (default: <repo>/datasets).",
    )
    args = parser.parse_args()
    target_dir = Path(args.target_dir).resolve()
    try:
        download_fashion_product_images_dataset(target_dir)
        csv_path, images_dir = resolve_fashion_product_images_dataset(target_dir)
    except (RuntimeError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Download complete.\nCSV: {csv_path}\nImages: {images_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
