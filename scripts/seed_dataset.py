"""CLI entry point for importing a Kaggle-style product dataset."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.dataset_paths import (
    DEFAULT_DATASET_ROOT,
    resolve_fashion_product_images_dataset,
)
from app.services.dataset_import_service import DatasetImportService
from app.services.embedding_service import EmbeddingService
from app.services.fashion_dataset_download import (
    download_fashion_product_images_dataset,
)
from app.services.image_service import ImageService
from app.services.vector_service import VectorService

FASHION_PRESET = "fashion-product-images-dataset"


def _build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser.

    Returns:
        Configured parser for the seed script.
    """
    parser = argparse.ArgumentParser(
        description="Import Kaggle CSV metadata and images into PostgreSQL and Qdrant.",
    )
    parser.add_argument(
        "--preset",
        choices=[FASHION_PRESET],
        help="Use built-in paths for a known Kaggle dataset layout.",
    )
    parser.add_argument(
        "--dataset-dir",
        default=str(DEFAULT_DATASET_ROOT),
        help="Dataset root used with --preset (default: <repo>/datasets).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the preset dataset from Kaggle before importing.",
    )
    parser.add_argument("--csv", help="Path to the dataset metadata CSV.")
    parser.add_argument(
        "--images-dir",
        help="Root directory containing dataset image files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of CSV rows to import (default: 100).",
    )
    parser.add_argument(
        "--currency",
        default="USD",
        help="Default currency when the CSV has no currency column (default: USD).",
    )
    return parser


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve CSV and image directory paths from CLI arguments.

    Args:
        args: Parsed command-line arguments.

    Returns:
        CSV path and images directory.

    Raises:
        SystemExit: When paths are missing or invalid.
    """
    dataset_root = Path(args.dataset_dir).resolve()
    if args.download:
        if args.preset != FASHION_PRESET:
            print("--download requires --preset fashion-product-images-dataset.", file=sys.stderr)
            raise SystemExit(1)
        try:
            download_fashion_product_images_dataset(dataset_root)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc
    if args.preset == FASHION_PRESET:
        try:
            return resolve_fashion_product_images_dataset(dataset_root)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc
    if not args.csv or not args.images_dir:
        print("Provide --csv and --images-dir, or use --preset.", file=sys.stderr)
        raise SystemExit(1)
    return Path(args.csv).resolve(), Path(args.images_dir).resolve()


def main() -> int:
    """Run the dataset import and print a summary.

    Returns:
        Process exit code (0 on success).
    """
    parser = _build_parser()
    args = parser.parse_args()
    if args.limit < 1:
        print("limit must be at least 1.", file=sys.stderr)
        return 1
    csv_path, images_dir = _resolve_paths(args)
    if not images_dir.is_dir():
        print(f"images directory not found: {images_dir}", file=sys.stderr)
        return 1
    embedding_service = EmbeddingService(model_name=settings.DINOV2_MODEL)
    embedding_service.load()
    vector_service = VectorService(
        url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION,
    )
    if not vector_service.ping():
        print("Qdrant is not reachable.", file=sys.stderr)
        return 1
    vector_service.ensure_collection(embedding_service.vector_size)
    image_service = ImageService(upload_dir=Path(settings.UPLOAD_DIR))
    image_service.ensure_upload_root()
    db = SessionLocal()
    try:
        importer = DatasetImportService(
            db=db,
            image_service=image_service,
            default_currency=args.currency,
        )
        stats = importer.import_from_csv(
            csv_path=csv_path,
            images_dir=images_dir,
            limit=args.limit,
            embedding_service=embedding_service,
            vector_service=vector_service,
        )
    finally:
        db.close()
    print(
        f"Done. attempted={stats.attempted} indexed={stats.indexed} skipped={stats.skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
