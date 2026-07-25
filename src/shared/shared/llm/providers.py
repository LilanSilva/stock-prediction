"""Provider adapter contract and response model.

Service code never imports a provider SDK directly. Adapters implement `LLMProvider` and are
injected into the gateway keyed by provider name, so switching providers is configuration only.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ProviderLLMResponse(BaseModel):
    """Normalized response returned by every provider adapter."""

    model_config = ConfigDict(frozen=True)

    content: dict[str, Any]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    response_hash: str = Field(min_length=1)
    raw_status: str = Field(min_length=1)


class LLMProvider(Protocol):
    """Adapter interface. Implementations wrap one provider SDK."""

    name: str

    async def complete_structured(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> ProviderLLMResponse: ...
