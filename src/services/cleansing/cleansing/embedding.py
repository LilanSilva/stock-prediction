"""Embedding backends and vector similarity.

`Embedder` is the stable interface used by the pipeline and clustering. Two backends are provided:

  - `HashingEmbedder` (default): a deterministic, dependency-light hashed bag-of-tokens projected to
    the configured dimension and L2-normalized. It has no cross-language semantics but lets the
    walking skeleton and unit tests run without downloading multi-GB models.
  - `BgeM3Embedder`: the real multilingual BAAI/bge-m3 backend (functional document sec 3/5), loaded
    lazily so the base install carries no torch/sentence-transformers dependency.

Both emit vectors of the same fixed dimension so the pgvector column type is stable.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from cleansing.exceptions import EmbeddingUnavailableError

_TOKEN = re.compile(r"\w+", re.UNICODE)


@runtime_checkable
class Embedder(Protocol):
    """Stable embedding interface (single text -> unit-norm float vector)."""

    @property
    def dimension(self) -> int: ...

    def is_ready(self) -> bool: ...

    async def embed(self, text: str) -> list[float]: ...


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. Returns 0.0 for a zero vector."""
    if len(left) != len(right):
        raise ValueError("vectors must have equal length")
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [v / norm for v in vector]


class HashingEmbedder:
    """Deterministic hashed-token embedder (POC default, no external model)."""

    def __init__(self, dimension: int = 1024) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def is_ready(self) -> bool:
        return True

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        return _l2_normalize(vector)


class BgeM3Embedder:
    """Real multilingual BAAI/bge-m3 backend, imported lazily.

    The model is loaded on first use and reused for every request (acceptance criterion 6: no
    per-article cold start). Encoding runs in a worker thread so it never blocks the event loop.
    """

    def __init__(self, model_name: str = "BAAI/bge-m3", dimension: int = 1024) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._model: object | None = None

    @property
    def dimension(self) -> int:
        return self._dimension

    def is_ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Eagerly load the model (called at startup so readiness reflects real availability)."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - only without the `ml` extra
            raise EmbeddingUnavailableError(
                "sentence-transformers not installed; install the 'ml' extra to use BgeM3Embedder"
            ) from exc
        self._model = SentenceTransformer(self._model_name)

    async def embed(self, text: str) -> list[float]:
        import asyncio

        if self._model is None:
            raise EmbeddingUnavailableError("BgeM3Embedder model is not loaded")
        model = self._model

        def _encode() -> list[float]:
            # normalize_embeddings=True yields unit-norm vectors for cosine via inner product.
            result = model.encode(  # type: ignore[attr-defined]
                text, normalize_embeddings=True
            )
            return [float(x) for x in result]

        return await asyncio.to_thread(_encode)


def build_embedder(
    backend: str, *, dimension: int, bge_model_name: str
) -> Embedder:
    """Construct the configured embedding backend."""
    normalized = backend.strip().lower()
    if normalized == "hashing":
        return HashingEmbedder(dimension=dimension)
    if normalized in {"bge-m3", "bge", "bgem3"}:
        return BgeM3Embedder(model_name=bge_model_name, dimension=dimension)
    raise EmbeddingUnavailableError(f"unknown embedding backend: {backend!r}")
