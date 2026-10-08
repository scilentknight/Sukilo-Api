"""Unit tests for embedding vector size mapping."""

from app.services.embedding_service import EmbeddingService


def test_dinov2_vits14_vector_size() -> None:
    service = EmbeddingService(model_name="dinov2_vits14")
    assert service.vector_size == 384


def test_dinov2_vitb14_vector_size() -> None:
    service = EmbeddingService(model_name="dinov2_vitb14")
    assert service.vector_size == 768


def test_dinov2_vitl14_vector_size() -> None:
    service = EmbeddingService(model_name="dinov2_vitl14")
    assert service.vector_size == 1024


def test_dinov2_vitg14_vector_size() -> None:
    service = EmbeddingService(model_name="dinov2_vitg14")
    assert service.vector_size == 1536
