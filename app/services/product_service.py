"""Vendor product creation with image storage and vector indexing."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.repositories.product_repository import ProductRepository
from app.services.embedding_service import EmbeddingService
from app.services.image_service import ImageService
from app.services.vector_service import VectorService
from app.utils.gs1 import parse_scanned_code
from app.utils.image_files import read_rgb_image, validate_image_count

logger = logging.getLogger(__name__)

VENDOR_SOURCE = "vendor"


class ProductService:
    """Create vendor products and index their images in Qdrant."""

    def __init__(
        self,
        db: Session,
        image_service: ImageService,
        max_upload_bytes: int,
    ) -> None:
        """Wire repository and upload helpers.

        Args:
            db: Request-scoped database session.
            image_service: Local file storage service.
            max_upload_bytes: Maximum allowed image size in bytes.
        """
        self._db = db
        self._repository = ProductRepository(db)
        self._image_service = image_service
        self._max_upload_bytes = max_upload_bytes

    def create_vendor_product(
        self,
        name: str,
        description: str | None,
        category: str,
        brand: str | None,
        price: Decimal,
        currency: str,
        images: list[UploadFile],
        embedding_service: EmbeddingService | None = None,
        vector_service: VectorService | None = None,
        gtin: str | None = None,
    ):
        """Persist a vendor product and index each uploaded image.

        Args:
            name: Product display name.
            description: Optional description text.
            category: Product category label.
            brand: Optional brand name.
            price: Decimal price amount.
            currency: ISO currency code.
            images: One to five image uploads.
            embedding_service: Loaded DINOv2 service.
            vector_service: Connected Qdrant service.
            gtin: Optional UPC, EAN, or GTIN from the product package.

        Returns:
            Created product ORM instance with images loaded.

        Raises:
            HTTPException: When validation or indexing fails.
        """
        validate_image_count(len(images))
        normalized_name = name.strip()
        normalized_category = category.strip()
        if not normalized_name or not normalized_category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="name and category are required.",
            )
        if price <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="price must be greater than zero.",
            )
        normalized_currency = currency.strip().upper()
        if len(normalized_currency) != 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="currency must be a 3-letter code.",
            )
        vendor_gtin = self._normalize_vendor_gtin(gtin)
        saved_paths: list[Path] = []
        try:
            product = self._repository.create_product(
                name=normalized_name,
                description=description.strip() if description else None,
                category=normalized_category,
                brand=brand.strip() if brand else None,
                price=price,
                currency=normalized_currency,
                source=VENDOR_SOURCE,
                gtin=vendor_gtin,
            )
            self._store_and_index_images(
                product,
                images,
                embedding_service,
                vector_service,
                saved_paths,
            )
            self._db.commit()
            self._db.refresh(product)
            return product
        except HTTPException:
            self._db.rollback()
            self._cleanup_paths(saved_paths)
            raise
        except IntegrityError as exc:
            self._db.rollback()
            self._cleanup_paths(saved_paths)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This barcode is already assigned to another product.",
            ) from exc
        except Exception as exc:
            logger.exception("Vendor product creation failed: %s", exc)
            self._db.rollback()
            self._cleanup_paths(saved_paths)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to create product.",
            ) from exc

    def _normalize_vendor_gtin(self, gtin: str | None) -> str | None:
        """Normalize an optional vendor barcode and reject duplicates.

        Args:
            gtin: Raw UPC, EAN, GTIN, or Digital Link, if provided.

        Returns:
            GTIN-14, or None when the vendor omitted a barcode.

        Raises:
            HTTPException: If the code is invalid or already used.
        """
        if gtin is None or not str(gtin).strip():
            return None
        try:
            normalized = parse_scanned_code(gtin)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        if self._repository.get_product_by_gtin(normalized):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This barcode is already assigned to another product.",
            )
        return normalized

    def _store_and_index_images(
        self,
        product,
        images: list[UploadFile],
        embedding_service: EmbeddingService | None,
        vector_service: VectorService | None,
        saved_paths: list[Path],
    ) -> None:
        """Save uploads and upsert image vectors for a product.

        Args:
            product: Persisted product row.
            images: Uploaded image files.
            embedding_service: Loaded DINOv2 service.
            vector_service: Connected Qdrant service.
            saved_paths: Mutable list of stored file paths for rollback.
        """
        for index, upload in enumerate(images):
            stored = self._image_service.save_product_image(product.id, upload)
            saved_paths.append(stored.path)
            product_image = self._repository.add_product_image(
                product_id=product.id,
                image_url=stored.url,
                is_primary=index == 0,
            )
            if embedding_service is not None and vector_service is not None:
                pil_image = read_rgb_image(upload, self._max_upload_bytes)
                vector = embedding_service.embed(pil_image)
                vector_service.upsert_product_image(
                    vector=vector,
                    product_id=product.id,
                    image_id=product_image.id,
                    category=product.category,
                    brand=product.brand,
                    source=product.source,
                )

    def get_product(self, product_id: int):
        """Return one product or raise when missing.

        Args:
            product_id: Product primary key.

        Returns:
            Product ORM instance.

        Raises:
            HTTPException: When the product does not exist.
        """
        product = self._repository.get_product(product_id)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found.",
            )
        return product

    def get_product_by_gtin(self, gtin: str):
        """Return one product by GTIN or raise when missing.

        Args:
            gtin: Fourteen-digit GTIN.

        Returns:
            Product ORM instance.

        Raises:
            HTTPException: When the product does not exist.
        """
        try:
            normalized = parse_scanned_code(gtin)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        product = self._repository.get_product_by_gtin(normalized)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No product matches this barcode.",
            )
        return product

    def get_product_by_scanned_code(self, raw_code: str):
        """Resolve a Digital Link or raw barcode to a product.

        Args:
            raw_code: Scanner output or typed barcode.

        Returns:
            Product ORM instance.

        Raises:
            HTTPException: When the code is invalid or unmatched.
        """
        return self.get_product_by_gtin(raw_code)

    def list_products(
        self,
        skip: int,
        limit: int,
        category: str | None = None,
        search: str | None = None,
    ):
        """Return a paginated list of products.

        Args:
            skip: Number of rows to skip.
            limit: Maximum rows to return.
            category: Optional exact category filter.
            search: Optional text search across name, brand, and category.

        Returns:
            List of product ORM instances.
        """
        normalized_search = search.strip() if search else None
        if normalized_search == "":
            normalized_search = None
        normalized_category = category.strip() if category else None
        if normalized_category == "":
            normalized_category = None
        return self._repository.list_products(
            skip=skip,
            limit=limit,
            category=normalized_category,
            search=normalized_search,
        )

    def list_categories(self):
        """Return category labels with product counts.

        Returns:
            List of (category name, count) tuples.
        """
        return self._repository.list_categories()

    def count_products(
        self,
        category: str | None = None,
        search: str | None = None,
    ) -> int:
        """Count products matching normalized catalog filters.

        Args:
            category: Optional exact category filter.
            search: Optional text search.

        Returns:
            Number of matching catalog products.
        """
        normalized_category = category.strip() if category else None
        normalized_search = search.strip() if search else None
        return self._repository.count_products(
            category=normalized_category or None,
            search=normalized_search or None,
        )

    def _cleanup_paths(self, paths: list[Path]) -> None:
        """Delete saved files after a failed transaction.

        Args:
            paths: Filesystem paths written during the request.
        """
        for path in paths:
            path.unlink(missing_ok=True)
