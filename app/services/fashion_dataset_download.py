"""Download the full Fashion Product Images dataset from Kaggle."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.core.dataset_paths import FASHION_KAGGLE_SLUG


def download_fashion_product_images_dataset(target_dir: Path) -> None:
    """Download and unzip the Kaggle dataset into ``target_dir``.

    Args:
        target_dir: Directory that will contain ``styles.csv`` and ``images/``.

    Raises:
        RuntimeError: If the Kaggle CLI is missing or the download fails.
    """
    kaggle_executable = shutil.which("kaggle")
    if not kaggle_executable:
        raise RuntimeError(
            "Kaggle CLI not found. Install with: pip install kaggle "
            "and configure ~/.kaggle/kaggle.json"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    command = [
        kaggle_executable,
        "datasets",
        "download",
        "-d",
        FASHION_KAGGLE_SLUG,
        "-p",
        str(target_dir),
        "--unzip",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Kaggle download failed for {FASHION_KAGGLE_SLUG}."
        ) from exc
