"""Import Kaggle-style product CSV rows into PostgreSQL and Qdrant."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.repositories.product_repository import ProductRepository
from app.services.embedding_service import EmbeddingService
from app.services.image_service import ImageService
from app.services.vector_service import VectorService

logger = logging.getLogger(__name__)

KAGGLE_SOURCE = "kaggle"
NAME_FIELDS = ("name", "productdisplayname", "product_name", "title")
CATEGORY_FIELDS = ("category", "mastercategory", "subcategory", "articletype")
BRAND_FIELDS = ("brand",)
PRICE_FIELDS = ("price",)
IMAGE_FIELDS = ("image_path", "image", "filename", "image_url", "link")
ID_FIELDS = ("id", "product_id", "sku")
DESCRIPTION_FIELDS = ("description", "gender", "basecolour", "color", "usage", "season")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


@dataclass(frozen=True)
class DatasetImportStats:
    """Summary counters for a dataset import run."""

    attempted: int
    indexed: int
    skipped: int


@dataclass(frozen=True)
class ParsedDatasetRow:
    """Normalized product fields extracted from one CSV row."""

    name: str
    category: str
    brand: str | None
    description: str | None
    price: Decimal
    currency: str
    image_path: Path


class DatasetImportService:
    """Seed catalog rows from a local Kaggle export."""

    def __init__(
        self,
        db: Session,
        image_service: ImageService,
        default_currency: str = "USD",
    ) -> None:
        """Attach persistence helpers.

        Args:
            db: SQLAlchemy session for the import run.
            image_service: Upload storage service for copied images.
            default_currency: Currency used when the CSV has no price currency column.
        """
        self._db = db
        self._repository = ProductRepository(db)
        self._image_service = image_service
        self._default_currency = default_currency.upper()

    def import_from_csv(
        self,
        csv_path: Path,
        images_dir: Path,
        limit: int,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
    ) -> DatasetImportStats:
        """Import up to ``limit`` rows from a metadata CSV file.

        Args:
            csv_path: Path to the Kaggle metadata CSV.
            images_dir: Root directory containing dataset images.
            limit: Maximum number of rows to index.
            embedding_service: Loaded DINOv2 service.
            vector_service: Connected Qdrant service.

        Returns:
            Attempt, success, and skip counters for the run.
        """
        rows = _read_csv_rows(csv_path, limit)
        indexed = 0
        skipped = 0
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            parsed = _parse_dataset_row(row, images_dir, self._default_currency)
            if parsed is None:
                skipped += 1
                logger.warning("Skipping row %s: missing required fields or image.", index)
                continue
            if not parsed.image_path.is_file():
                skipped += 1
                logger.warning("Skipping row %s: image not found at %s", index, parsed.image_path)
                continue
            try:
                product = self._repository.create_product(
                    name=parsed.name,
                    description=parsed.description,
                    category=parsed.category,
                    brand=parsed.brand,
                    price=parsed.price,
                    currency=parsed.currency,
                    source=KAGGLE_SOURCE,
                )
                stored = self._image_service.copy_product_image_from_path(
                    product_id=product.id,
                    source_path=parsed.image_path,
                )
                product_image = self._repository.add_product_image(
                    product_id=product.id,
                    image_url=stored.url,
                    is_primary=True,
                )
                pil_image = _load_rgb_image(parsed.image_path)
                vector = embedding_service.embed(pil_image)
                vector_service.upsert_product_image(
                    vector=vector,
                    product_id=product.id,
                    image_id=product_image.id,
                    category=product.category,
                    brand=product.brand,
                    source=product.source,
                )
                self._db.commit()
                indexed += 1
                print(f"[{index}/{total}] Indexed product {product.id}")
            except (OSError, UnidentifiedImageError, ValueError) as exc:
                self._db.rollback()
                skipped += 1
                logger.warning("Skipping row %s after error: %s", index, exc)
            except Exception as exc:
                self._db.rollback()
                skipped += 1
                logger.exception("Skipping row %s after unexpected error: %s", index, exc)
        return DatasetImportStats(attempted=total, indexed=indexed, skipped=skipped)


def _read_csv_rows(csv_path: Path, limit: int) -> list[dict[str, str]]:
    """Load CSV rows up to the requested limit.

    Args:
        csv_path: Metadata CSV path.
        limit: Maximum rows to return.

    Returns:
        List of row dictionaries keyed by header name.

    Raises:
        FileNotFoundError: If the CSV does not exist.
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, str]] = []
        for row in reader:
            if len(rows) >= limit:
                break
            rows.append({key: (value or "").strip() for key, value in row.items() if key})
        return rows


def _parse_dataset_row(
    row: dict[str, str],
    images_dir: Path,
    default_currency: str,
) -> ParsedDatasetRow | None:
    """Map a CSV row into normalized import fields.

    Args:
        row: Raw CSV row.
        images_dir: Dataset image root directory.
        default_currency: Currency used when price exists without a currency column.

    Returns:
        Parsed row or None when required data is missing.
    """
    name = _pick_field(row, NAME_FIELDS)
    category = _pick_field(row, CATEGORY_FIELDS)
    if not name or not category:
        return None
    image_path = _resolve_image_from_row(row, images_dir)
    if image_path is None:
        return None
    brand = _pick_field(row, BRAND_FIELDS)
    description = _build_description(row)
    price = _parse_price(_pick_field(row, PRICE_FIELDS))
    return ParsedDatasetRow(
        name=name,
        category=category,
        brand=brand,
        description=description,
        price=price,
        currency=default_currency,
        image_path=image_path,
    )


def _pick_field(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    """Return the first non-empty candidate column value.

    Args:
        row: CSV row with original header casing.
        candidates: Lowercase header names to try.

    Returns:
        Trimmed value or None.
    """
    lowered = {key.lower(): value for key, value in row.items()}
    for candidate in candidates:
        value = lowered.get(candidate)
        if value:
            return value.strip()
    return None


def _resolve_image_from_row(row: dict[str, str], images_dir: Path) -> Path | None:
    """Resolve the on-disk image path for a CSV row.

    Args:
        row: Raw CSV row.
        images_dir: Dataset image root directory.

    Returns:
        Existing image path, or None when no file can be resolved.
    """
    image_value = _pick_field(row, IMAGE_FIELDS)
    if image_value:
        candidate = _resolve_image_path(images_dir, image_value)
        return candidate if candidate.is_file() else None
    row_id = _pick_field(row, ID_FIELDS)
    if not row_id:
        return None
    for suffix in IMAGE_SUFFIXES:
        candidate = images_dir / f"{row_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _resolve_image_path(images_dir: Path, image_value: str) -> Path:
    """Resolve a CSV image reference to an on-disk path.

    Args:
        images_dir: Dataset image root.
        image_value: Relative or absolute image path from the CSV.

    Returns:
        Resolved filesystem path.
    """
    candidate = Path(image_value)
    if candidate.is_absolute():
        return candidate
    return images_dir / candidate


def _build_description(row: dict[str, str]) -> str | None:
    """Combine optional descriptive CSV columns.

    Args:
        row: CSV row.

    Returns:
        Combined description or None.
    """
    parts: list[str] = []
    for field in DESCRIPTION_FIELDS:
        value = _pick_field(row, (field,))
        if value:
            parts.append(value)
    if not parts:
        return None
    return " | ".join(parts)


def _parse_price(raw_price: str | None) -> Decimal:
    """Parse a CSV price or return a demo default.

    Args:
        raw_price: Price string from the CSV.

    Returns:
        Decimal price greater than zero.
    """
    if not raw_price:
        return Decimal("1.00")
    cleaned = raw_price.replace(",", "").strip()
    try:
        price = Decimal(cleaned)
    except InvalidOperation:
        return Decimal("1.00")
    if price <= 0:
        return Decimal("1.00")
    return price


def _load_rgb_image(path: Path) -> Image.Image:
    """Open an on-disk image as RGB.

    Args:
        path: Image file path.

    Returns:
        RGB PIL image.

    Raises:
        UnidentifiedImageError: If Pillow cannot decode the file.
    """
    with Image.open(path) as image:
        return image.convert("RGB")
