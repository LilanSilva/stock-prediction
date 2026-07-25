"""Provider-configurable LLM gateway.

Stable interface for all LLM access (see the Cleansing functional document sec 6 and
backlog/.../T03-llm-gateway-wrapper.md). Responsibilities:
  - select the provider adapter by configured name (never hardcode a provider in service logic);
  - enforce compact input/output budgets before invocation;
  - validate structured output against the caller schema, retrying malformed output at most once;
  - cache by full identity so a replayed request makes zero provider calls;
  - record provider usage metadata from the response, never via an extra LLM call.

M1 usage rule (POC-6 STOP): only the Cleansing Service may call this gateway. Prediction must not.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

import structlog
from pydantic import BaseModel

from shared.llm.cache import InMemoryLLMCache, LLMCache, build_cache_key
from shared.llm.exceptions import (
    LLMConfigurationError,
    LLMMalformedOutputError,
    LLMTransportError,
)
from shared.llm.providers import LLMProvider
from shared.llm.settings import LLMSettings
from shared.llm.validation import validate_against_schema, validate_schema

logger = structlog.get_logger(__name__)


class LLMResult(BaseModel):
    content: dict[str, Any]
    provider: str
    model: str
    prompt_version: str
    context_hash: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    attempt_count: int
    cache_hit: bool
    status: Literal["SUCCESS", "FAILED"]
    response_hash: str | None = None


def _context_hash(messages: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(messages, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Cheap heuristic input-size estimate (~4 chars/token). Never calls the model to count."""
    chars = sum(len(m.get("content", "")) for m in messages)
    return chars // 4


class LLMGateway:
    def __init__(
        self,
        settings: LLMSettings,
        providers: Mapping[str, LLMProvider],
        cache: LLMCache | None = None,
    ) -> None:
        self._settings = settings
        self._providers = providers
        self._cache: LLMCache = cache or InMemoryLLMCache()

    def _select_provider(self) -> LLMProvider:
        self._settings.require_configured()
        provider = self._providers.get(self._settings.provider.lower())
        if provider is None:
            raise LLMConfigurationError(
                f"no adapter registered for LLM_PROVIDER={self._settings.provider!r}"
            )
        return provider

    async def complete_structured(
        self,
        *,
        task: str,
        prompt_version: str,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
        correlation_id: str,
        cache_key: str | None = None,
    ) -> LLMResult:
        provider = self._select_provider()
        context_hash = cache_key or _context_hash(messages)

        try:
            validate_schema(output_schema)
        except ValueError as exc:
            raise LLMConfigurationError(str(exc)) from exc

        # Enforce the compact-input budget before spending a provider call.
        if _estimate_tokens(messages) > self._settings.max_input_tokens:
            raise LLMConfigurationError(
                f"estimated input exceeds LLM_MAX_INPUT_TOKENS={self._settings.max_input_tokens}"
            )

        key = build_cache_key(
            provider=provider.name,
            model=self._settings.model,
            task=task,
            prompt_version=prompt_version,
            output_schema=output_schema,
            context_hash=context_hash,
        )

        if self._settings.cache_enabled:
            cached = await self._cache.get(key)
            if cached is not None:
                logger.info(
                    "llm_cache_hit",
                    correlation_id=correlation_id,
                    task=task,
                    provider=cached.provider,
                    model=cached.model,
                    prompt_version=prompt_version,
                    input_tokens=cached.input_tokens,
                    output_tokens=cached.output_tokens,
                    latency_ms=cached.latency_ms,
                )
                return LLMResult(
                    content=cached.content,
                    provider=cached.provider,
                    model=cached.model,
                    prompt_version=prompt_version,
                    context_hash=context_hash,
                    input_tokens=cached.input_tokens,
                    output_tokens=cached.output_tokens,
                    latency_ms=cached.latency_ms,
                    attempt_count=0,
                    cache_hit=True,
                    status="SUCCESS",
                    response_hash=cached.response_hash,
                )

        max_attempts = 1 + max(0, self._settings.malformed_output_retries)
        attempt = 0
        last_error: Exception | None = None
        while attempt < max_attempts:
            attempt += 1
            try:
                response = await provider.complete_structured(
                    model=self._settings.model,
                    messages=messages,
                    output_schema=output_schema,
                    max_output_tokens=self._settings.max_output_tokens,
                    timeout_seconds=self._settings.timeout_seconds,
                )
            except LLMTransportError:
                # Transport/429/5xx retries are the adapter's responsibility; a raised transport
                # error here is terminal for this call.
                logger.error(
                    "llm_transport_failed",
                    correlation_id=correlation_id,
                    task=task,
                    provider=provider.name,
                    model=self._settings.model,
                    attempt_count=attempt,
                )
                raise
            except LLMMalformedOutputError as exc:
                last_error = exc
                continue

            try:
                validate_against_schema(response.content, output_schema)
            except ValueError as exc:
                last_error = exc
                continue

            if self._settings.cache_enabled:
                await self._cache.set(key, response)

            logger.info(
                "llm_request_completed",
                correlation_id=correlation_id,
                task=task,
                provider=response.provider,
                model=response.model,
                prompt_version=prompt_version,
                context_hash=context_hash,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
                attempt_count=attempt,
                response_hash=response.response_hash,
            )
            return LLMResult(
                content=response.content,
                provider=response.provider,
                model=response.model,
                prompt_version=prompt_version,
                context_hash=context_hash,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=response.latency_ms,
                attempt_count=attempt,
                cache_hit=False,
                status="SUCCESS",
                response_hash=response.response_hash,
            )

        logger.error(
            "llm_structured_output_failed",
            correlation_id=correlation_id,
            task=task,
            provider=provider.name,
            model=self._settings.model,
            attempt_count=attempt,
        )
        raise LLMMalformedOutputError(
            f"structured output failed validation after {attempt} attempt(s): {last_error}"
        )
