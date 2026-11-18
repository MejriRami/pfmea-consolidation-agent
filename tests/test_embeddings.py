import json

import pytest

from pfmea_agents.embeddings import EmbeddingCache, cosine_similarity, embedding_text


def test_cosine_similarity_identical_vectors_is_one():
    v = [0.1, 0.2, 0.3, 0.4]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_is_scale_invariant():
    a = [1.0, 2.0, 3.0]
    b = [2.0, 4.0, 6.0]  # same direction, different magnitude
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_cosine_similarity_dimension_mismatch_raises():
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cosine_similarity_zero_vector_is_zero_not_nan():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_embedding_text_concatenates_mode_and_cause():
    assert embedding_text("Wrong core dimensions", "Deviation on supplier process") == (
        "Wrong core dimensions Deviation on supplier process"
    )


def test_embedding_text_strips_surrounding_whitespace():
    assert embedding_text("Wrong core dimensions", "") == "Wrong core dimensions"


def test_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "cache.json"
    cache = EmbeddingCache(path)
    assert cache.get("row1", "some text") is None

    cache.set("row1", "some text", [0.1, 0.2, 0.3])
    cache.save()

    reloaded = EmbeddingCache(path)
    assert reloaded.get("row1", "some text") == [0.1, 0.2, 0.3]


def test_cache_invalidates_on_text_change():
    """Keyed by row_id per the requirement, but must not silently return a
    stale vector if the row's normalized text changed between runs."""
    cache = EmbeddingCache.__new__(EmbeddingCache)
    cache.path = None
    cache._entries = {}
    cache._hits = 0
    cache._misses = 0

    cache.set("row1", "original text", [1.0, 2.0])
    assert cache.get("row1", "original text") == [1.0, 2.0]
    assert cache.get("row1", "different text now") is None


def test_cache_missing_file_starts_empty(tmp_path):
    cache = EmbeddingCache(tmp_path / "does_not_exist.json")
    assert cache.get("anything", "text") is None
    assert cache.stats["entries"] == 0


def test_cache_tracks_hit_miss_stats():
    cache = EmbeddingCache.__new__(EmbeddingCache)
    cache.path = None
    cache._entries = {}
    cache._hits = 0
    cache._misses = 0

    cache.get("row1", "text")  # miss
    cache.set("row1", "text", [1.0])
    cache.get("row1", "text")  # hit
    cache.get("row1", "other text")  # miss (text changed)

    assert cache.stats == {"hits": 1, "misses": 2, "entries": 1}
