"""LLM response cache.

Cache identity includes provider, model, task, prompt version, schema hash, and context/input
hash (docs/functional-documents/cleansing-service-functional-document.md sec 6). Replaying the same
completed request must return the cached response and make zero provider calls.

The default implementation is a process-local in-memory cache, sufficient for the POC and tests.
Services may inject a persistent implementation later without changing the gateway.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from shared.llm.providers import ProviderLLMResponse


def build_cache_key(
    *,
    provider: str,
    model: str,
    task: str,
    prompt_version: str,
    output_schema: dict[str, Any],
    context_hash: str,
) -> str:
    """Deterministic cache key over the full identity tuple."""
    schema_hash = hashlib.sha256(
        json.dumps(output_schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    identity = "|".join([provider, model, task, prompt_version, schema_hash, context_hash])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class LLMCache(Protocol):
    async def get(self, key: str) -> ProviderLLMResponse | None: ...

    async def set(self, key: str, value: ProviderLLMResponse) -> None: ...


class InMemoryLLMCache:
    """Process-local cache. Not shared across processes; fine for POC and unit tests."""

    def __init__(self) -> None:
        self._store: dict[str, ProviderLLMResponse] = {}

    async def get(self, key: str) -> ProviderLLMResponse | None:
        return self._store.get(key)

    async def set(self, key: str, value: ProviderLLMResponse) -> None:
        self._store[key] = value
