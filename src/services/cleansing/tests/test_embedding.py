from __future__ import annotations

import math

import pytest

from cleansing.embedding import HashingEmbedder, build_embedder, cosine_similarity
from cleansing.exceptions import EmbeddingUnavailableError


async def test_hashing_embedder_dimension_and_norm() -> None:
    embedder = HashingEmbedder(dimension=1024)
    assert embedder.dimension == 1024
    assert embedder.is_ready()
    vector = await embedder.embed("gold prices rise sharply today")
    assert len(vector) == 1024
    # L2-normalized (unit length) unless the text had no tokens.
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-6)


async def test_hashing_embedder_is_deterministic() -> None:
    embedder = HashingEmbedder(dimension=256)
    a = await embedder.embed("OPEC cuts output")
    b = await embedder.embed("OPEC cuts output")
    assert a == b
    assert math.isclose(cosine_similarity(a, b), 1.0, rel_tol=1e-6)


async def test_similar_texts_have_higher_similarity() -> None:
    embedder = HashingEmbedder(dimension=1024)
    base = await embedder.embed("central bank raises interest rates to fight inflation")
    close = await embedder.embed("central bank raises interest rates amid inflation")
    far = await embedder.embed("hurricane makes landfall on the gulf coast")
    assert cosine_similarity(base, close) > cosine_similarity(base, far)


def test_cosine_zero_vector() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch() -> None:
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])


def test_build_embedder_unknown() -> None:
    with pytest.raises(EmbeddingUnavailableError):
        build_embedder("nope", dimension=8, bge_model_name="x")
