"""Qdrant vector storage and similarity search."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

logger = logging.getLogger(__name__)


class VectorService:
    """Manage the product image collection in Qdrant."""

    def __init__(self, url: str, collection_name: str) -> None:
        """Create a client for the configured Qdrant instance.

        Args:
            url: Qdrant HTTP base URL.
            collection_name: Collection used for product image vectors.
        """
        self._client = QdrantClient(url=url)
        self._collection_name = collection_name

    @property
    def collection_name(self) -> str:
        """Return the active Qdrant collection name."""
        return self._collection_name

    def ensure_collection(self, vector_size: int) -> None:
        """Create the collection when it does not already exist.

        Args:
            vector_size: Embedding dimension stored per point.
        """
        if self._client.collection_exists(self._collection_name):
            return
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=qdrant_models.VectorParams(
                size=vector_size,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection %s", self._collection_name)

    def upsert_point(
        self,
        vector: list[float],
        payload: dict[str, Any],
        point_id: str | int | None = None,
    ) -> str:
        """Insert or update one vector point.

        Args:
            vector: Normalized embedding.
            payload: Searchable metadata stored with the vector.
            point_id: Optional unsigned integer or UUID string; a UUID is generated when omitted.

        Returns:
            The Qdrant point identifier that was written, as a string.
        """
        resolved_id: str | int = point_id if point_id is not None else str(uuid.uuid4())
        self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=resolved_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )
        return str(resolved_id)

    def upsert_product_image(
        self,
        vector: list[float],
        product_id: int,
        image_id: int,
        category: str,
        brand: str | None,
        source: str,
    ) -> str:
        """Store one product image embedding with catalog metadata.

        Args:
            vector: Normalized DINOv2 embedding.
            product_id: PostgreSQL product identifier.
            image_id: PostgreSQL product image identifier.
            category: Product category label.
            brand: Optional brand name.
            source: Product origin such as vendor or kaggle.

        Returns:
            Qdrant point identifier (the image id as a string).
        """
        payload: dict[str, Any] = {
            "product_id": product_id,
            "image_id": image_id,
            "category": category,
            "source": source,
        }
        if brand:
            payload["brand"] = brand
        return self.upsert_point(
            vector=vector,
            payload=payload,
            point_id=image_id,
        )

    def search(
        self,
        vector: list[float],
        limit: int = 5,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find nearest neighbors for a query vector.

        Args:
            vector: Normalized query embedding.
            limit: Maximum number of hits to return.
            category: Optional exact category filter on point payload.

        Returns:
            Ranked hits with point id, score, and payload.
        """
        query_filter: qdrant_models.Filter | None = None
        if category:
            query_filter = qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="category",
                        match=qdrant_models.MatchValue(value=category),
                    )
                ]
            )
        results = self._client.query_points(
            collection_name=self._collection_name,
            query=vector,
            limit=limit,
            query_filter=query_filter,
        )
        hits: list[dict[str, Any]] = []
        for point in results.points:
            hits.append(
                {
                    "point_id": str(point.id),
                    "score": float(point.score),
                    "payload": point.payload or {},
                }
            )
        return hits

    def ping(self) -> bool:
        """Check whether Qdrant responds to a collections list call.

        Returns:
            True when Qdrant is reachable.
        """
        try:
            self._client.get_collections()
            return True
        except Exception as exc:
            logger.error("Qdrant health check failed: %s", exc)
            return False
