"""Concrete provider adapters.

The OpenAI adapter is provided as a real integration path and as a template for future adapters
(anthropic, bedrock). It imports the provider SDK lazily, so the base shared package installs
without the optional `llm` extra and services that never call it carry no provider dependency.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, cast

from shared.llm.exceptions import LLMConfigurationError, LLMMalformedOutputError, LLMTransportError
from shared.llm.providers import LLMProvider, ProviderLLMResponse
from shared.llm.settings import LLMSettings


def _hash_content(content: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OpenAIProvider:
    """Adapter for OpenAI-compatible structured output via the Responses/Chat JSON schema mode."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        transport_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._transport_retries = transport_retries

    async def complete_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProviderLLMResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without the llm extra
            raise LLMTransportError(
                "openai package not installed; install the 'llm' extra to use OpenAIProvider"
            ) from exc

        client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=timeout_seconds,
            max_retries=self._transport_retries,
        )
        started = time.monotonic()
        try:
            # The provider SDK is optional and only type-checked when installed; cast the call to
            # Any so strict typing passes with or without the `llm` extra present.
            create = cast(Any, client.chat.completions.create)
            completion = await create(
                model=model,
                messages=messages,
                max_tokens=max_output_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "structured_output", "schema": output_schema},
                },
            )
        except Exception as exc:  # noqa: BLE001 - normalized to a typed transport error
            raise LLMTransportError(f"openai request failed: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        raw = completion.choices[0].message.content or "{}"
        try:
            content = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMMalformedOutputError(f"openai returned non-JSON content: {exc}") from exc
        if not isinstance(content, dict):
            raise LLMMalformedOutputError("openai structured output must be a JSON object")

        usage = completion.usage
        return ProviderLLMResponse(
            content=content,
            provider=self.name,
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            response_hash=_hash_content(content),
            raw_status="ok",
        )


def build_provider(settings: LLMSettings) -> LLMProvider:
    """Build the configured provider adapter and enforce readiness credentials."""
    settings.require_configured()
    if settings.provider.lower() == "openai":
        return OpenAIProvider(
            settings.require_api_key(),
            base_url=settings.resolve_base_url(),
            transport_retries=settings.transport_retries,
        )
    raise LLMConfigurationError(
        f"no built-in adapter is available for LLM_PROVIDER={settings.provider!r}"
    )
