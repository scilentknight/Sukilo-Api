"""Customer visual search over Qdrant and PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PIL import Image
from sqlalchemy.orm import Session

from app.models.product import Product
from app.repositories.product_repository import ProductRepository
from app.schemas.search import ImageSearchResponse, ImageSearchResult
from app.services.embedding_service import EmbeddingService
from app.services.vector_service import VectorService

DEFAULT_PRODUCT_LIMIT = 20
MAX_PRODUCT_LIMIT = 50
MIN_VECTOR_HITS = 50
VECTOR_HIT_CAP = 200
DEFAULT_MIN_SIMILARITY = 0.25


@dataclass(frozen=True)
class RankedProductMatch:
    """Best image match for one product after vector deduplication."""

    product_id: int
    similarity: float
    image_id: int | None


def rank_product_matches_from_hits(
    hits: list[dict[str, Any]],
    limit: int,
) -> list[RankedProductMatch]:
    """Keep the highest-scoring hit per product and return a ranked slice.

    Args:
        hits: Qdrant search hits with score and payload metadata.
        limit: Maximum distinct products to return.

    Returns:
        Product matches ordered by descending similarity.
    """
    best_by_product: dict[int, RankedProductMatch] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        product_id = payload.get("product_id")
        if product_id is None:
            continue
        try:
            normalized_product_id = int(product_id)
        except (TypeError, ValueError):
            continue
        score = float(hit["score"])
        image_id_raw = payload.get("image_id")
        image_id: int | None
        if image_id_raw is None:
            image_id = None
        else:
            try:
                image_id = int(image_id_raw)
            except (TypeError, ValueError):
                image_id = None
        existing = best_by_product.get(normalized_product_id)
        if existing is None or score > existing.similarity:
            best_by_product[normalized_product_id] = RankedProductMatch(
                product_id=normalized_product_id,
                similarity=score,
                image_id=image_id,
            )
    ranked = sorted(
        best_by_product.values(),
        key=lambda match: match.similarity,
        reverse=True,
    )
    return ranked[:limit]


def filter_matches_by_similarity(
    matches: list[RankedProductMatch],
    min_similarity: float,
    limit: int,
) -> list[RankedProductMatch]:
    """Drop weak vector matches below a cosine similarity floor.

    Args:
        matches: Ranked product matches from Qdrant.
        min_similarity: Minimum score to include in API results.
        limit: Maximum matches to return after filtering.

    Returns:
        Filtered matches still ordered by descending similarity.
    """
    kept = [match for match in matches if match.similarity >= min_similarity]
    return kept[:limit]


def vector_hit_limit(product_limit: int) -> int:
    """Choose how many image vectors to fetch before product deduplication.

    Args:
        product_limit: Desired number of distinct products.

    Returns:
        Qdrant ``limit`` argument sized for deduplication.
    """
    scaled = max(MIN_VECTOR_HITS, product_limit * 3)
    return min(scaled, VECTOR_HIT_CAP)


def resolve_display_image_url(product: Product, image_id: int | None) -> str:
    """Pick the matched image URL or fall back to the catalog thumbnail.

    Args:
        product: Product with related images loaded.
        image_id: Qdrant payload image identifier when present.

    Returns:
        Public URL path for the result card.

    Raises:
        ValueError: When the product has no images.
    """
    if not product.images:
        raise ValueError("Product has no images.")
    if image_id is not None:
        for product_image in product.images:
            if product_image.id == image_id:
                return product_image.image_url
    for product_image in product.images:
        if product_image.is_primary:
            return product_image.image_url
    return product.images[0].image_url


class SearchService:
    """Embed query images and return similar catalog products."""

    def __init__(self, db: Session) -> None:
        """Attach a product repository to the request session.

        Args:
            db: Request-scoped database session.
        """
        self._repository = ProductRepository(db)

    def search_by_image(
        self,
        image: Image.Image,
        limit: int,
        category: str | None,
        min_similarity: float,
        embedding_service: EmbeddingService,
        vector_service: VectorService,
    ) -> ImageSearchResponse:
        """Find visually similar products for a query image.

        Args:
            image: RGB query image.
            limit: Maximum distinct products to return.
            category: Optional exact category filter applied in Qdrant.
            min_similarity: Minimum cosine score for a product to appear.
            embedding_service: Loaded DINOv2 service.
            vector_service: Connected Qdrant service.

        Returns:
            Ranked product metadata enriched from PostgreSQL.
        """
        vector = embedding_service.embed(image)
        hits = vector_service.search(
            vector=vector,
            limit=vector_hit_limit(limit),
            category=category,
        )
        ranked = rank_product_matches_from_hits(hits=hits, limit=limit * 3)
        ranked = filter_matches_by_similarity(
            matches=ranked,
            min_similarity=min_similarity,
            limit=limit,
        )
        if not ranked:
            return ImageSearchResponse(results=[])
        product_ids = [match.product_id for match in ranked]
        products = self._repository.get_products_by_ids(product_ids)
        products_by_id = {product.id: product for product in products}
        results: list[ImageSearchResult] = []
        for match in ranked:
            product = products_by_id.get(match.product_id)
            if product is None:
                continue
            try:
                image_url = resolve_display_image_url(product, match.image_id)
            except ValueError:
                continue
            results.append(
                ImageSearchResult(
                    product_id=product.id,
                    name=product.name,
                    brand=product.brand,
                    category=product.category,
                    price=product.price,
                    currency=product.currency,
                    image_url=image_url,
                    similarity=round(match.similarity, 4),
                )
            )
        return ImageSearchResponse(results=results)
