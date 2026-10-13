"""Application startup and shutdown hooks."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.config import settings
from app.modules.access.bootstrap import configure_access
from app.modules.customers.bootstrap import configure_customers
from app.modules.identity.bootstrap import configure_identity
from app.services.embedding_service import EmbeddingService
from app.services.vector_service import VectorService

logger = logging.getLogger(__name__)


def _initialize_vector_stack(app: FastAPI) -> None:
    """Load DINOv2 and prepare the Qdrant collection.

    Args:
        app: FastAPI application instance.

    Raises:
        RuntimeError: If Qdrant is unreachable or the model fails to load.
    """
    embedding_service = EmbeddingService(model_name=settings.DINOV2_MODEL)
    embedding_service.load()
    vector_service = VectorService(
        url=settings.QDRANT_URL,
        collection_name=settings.QDRANT_COLLECTION,
    )
    if not vector_service.ping():
        raise RuntimeError("Qdrant is not reachable.")
    vector_service.ensure_collection(embedding_service.vector_size)
    app.state.embedding_service = embedding_service
    app.state.vector_service = vector_service
    logger.info("Vector stack initialized.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize optional services on startup and release them on shutdown.

    Args:
        app: FastAPI application instance.

    Yields:
        Control to the running application.
    """
    app.state.embedding_service = None
    app.state.vector_service = None
    app.state.identity_application_factory = None
    app.state.access_application_factory = None
    app.state.customer_application_factory = None
    configure_identity(app, settings)
    configure_access(app, settings)
    configure_customers(app, settings)
    if settings.ENABLE_VECTOR_STACK:
        _initialize_vector_stack(app)
    yield
    app.state.customer_application_factory = None
    app.state.access_application_factory = None
    app.state.identity_application_factory = None
    app.state.embedding_service = None
    app.state.vector_service = None
