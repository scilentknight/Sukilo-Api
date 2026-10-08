"""Unit tests for visual search ranking helpers."""

from types import SimpleNamespace

from app.services.search_service import (
    RankedProductMatch,
    filter_matches_by_similarity,
    rank_product_matches_from_hits,
    resolve_display_image_url,
    vector_hit_limit,
)


def test_rank_product_matches_keeps_best_score_per_product() -> None:
    hits = [
        {"score": 0.7, "payload": {"product_id": 1, "image_id": 10}},
        {"score": 0.9, "payload": {"product_id": 1, "image_id": 11}},
        {"score": 0.85, "payload": {"product_id": 2, "image_id": 20}},
    ]
    ranked = rank_product_matches_from_hits(hits=hits, limit=10)
    assert [match.product_id for match in ranked] == [1, 2]
    assert ranked[0].similarity == 0.9
    assert ranked[0].image_id == 11


def test_rank_product_matches_respects_limit() -> None:
    hits = [
        {"score": 0.5, "payload": {"product_id": 1, "image_id": 1}},
        {"score": 0.6, "payload": {"product_id": 2, "image_id": 2}},
        {"score": 0.7, "payload": {"product_id": 3, "image_id": 3}},
    ]
    ranked = rank_product_matches_from_hits(hits=hits, limit=2)
    assert len(ranked) == 2
    assert ranked[0].product_id == 3


def test_filter_matches_by_similarity_drops_weak_hits() -> None:
    matches = [
        RankedProductMatch(product_id=1, similarity=0.9, image_id=1),
        RankedProductMatch(product_id=2, similarity=0.49, image_id=2),
        RankedProductMatch(product_id=3, similarity=0.08, image_id=3),
    ]
    filtered = filter_matches_by_similarity(
        matches=matches,
        min_similarity=0.50,
        limit=10,
    )
    assert len(filtered) == 1
    assert filtered[0].product_id == 1


def test_vector_hit_limit_scales_with_product_limit() -> None:
    assert vector_hit_limit(20) == 60
    assert vector_hit_limit(5) == 50


def test_resolve_display_image_url_prefers_matched_image() -> None:
    product = SimpleNamespace(
        images=[
            SimpleNamespace(id=1, image_url="/uploads/a.jpg", is_primary=True),
            SimpleNamespace(id=2, image_url="/uploads/b.jpg", is_primary=False),
        ]
    )
    assert resolve_display_image_url(product, 2) == "/uploads/b.jpg"


def test_resolve_display_image_url_falls_back_to_primary() -> None:
    product = SimpleNamespace(
        images=[
            SimpleNamespace(id=1, image_url="/uploads/a.jpg", is_primary=False),
            SimpleNamespace(id=2, image_url="/uploads/b.jpg", is_primary=True),
        ]
    )
    assert resolve_display_image_url(product, 99) == "/uploads/b.jpg"
