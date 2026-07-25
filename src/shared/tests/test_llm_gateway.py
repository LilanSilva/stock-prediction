import os
from typing import Any

import pytest

from shared.llm.adapters import build_provider
from shared.llm.exceptions import (
    LLMConfigurationError,
    LLMMalformedOutputError,
)
from shared.llm.gateway import LLMGateway
from shared.llm.providers import ProviderLLMResponse
from shared.llm.settings import LLMSettings

SCHEMA = {
    "type": "object",
    "required": ["direction", "confidence"],
    "properties": {
        "direction": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

MESSAGES = [{"role": "user", "content": "resolve this"}]


class FakeProvider:
    name = "fake"

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self.calls = 0

    async def complete_structured(self, **_: object) -> ProviderLLMResponse:
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return ProviderLLMResponse(
            content=content,
            provider=self.name,
            model="fake-model",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            response_hash="hash",
            raw_status="ok",
        )


def _settings(**overrides: object) -> LLMSettings:
    base = dict(provider="fake", model="fake-model", cache_enabled=True)
    base.update(overrides)
    return LLMSettings(**base)  # type: ignore[arg-type]


async def test_missing_provider_config_fails_fast() -> None:
    gw = LLMGateway(LLMSettings(provider="", model=""), {})
    with pytest.raises(LLMConfigurationError):
        await gw.complete_structured(
            task="t", prompt_version="v1", messages=MESSAGES, output_schema=SCHEMA,
            correlation_id="c",
        )


async def test_unregistered_provider_fails() -> None:
    gw = LLMGateway(_settings(provider="ghost"), {})
    with pytest.raises(LLMConfigurationError):
        await gw.complete_structured(
            task="t", prompt_version="v1", messages=MESSAGES, output_schema=SCHEMA,
            correlation_id="c",
        )


async def test_valid_structured_output_succeeds() -> None:
    provider = FakeProvider([{"direction": "UP", "confidence": 0.7}])
    gw = LLMGateway(_settings(), {"fake": provider})
    result = await gw.complete_structured(
        task="t", prompt_version="v1", messages=MESSAGES, output_schema=SCHEMA,
        correlation_id="c", cache_key="ctx1",
    )
    assert result.status == "SUCCESS"
    assert result.attempt_count == 1
    assert result.cache_hit is False
    assert provider.calls == 1


async def test_malformed_then_valid_uses_one_retry() -> None:
    provider = FakeProvider([{"direction": "UP"}, {"direction": "UP", "confidence": 0.7}])
    gw = LLMGateway(_settings(malformed_output_retries=1), {"fake": provider})
    result = await gw.complete_structured(
        task="t", prompt_version="v1", messages=MESSAGES, output_schema=SCHEMA,
        correlation_id="c", cache_key="ctx2",
    )
    assert result.attempt_count == 2
    assert provider.calls == 2


async def test_persistently_malformed_raises() -> None:
    provider = FakeProvider([{"direction": "UP"}])  # missing confidence, always
    gw = LLMGateway(_settings(malformed_output_retries=1), {"fake": provider})
    with pytest.raises(LLMMalformedOutputError):
        await gw.complete_structured(
            task="t", prompt_version="v1", messages=MESSAGES, output_schema=SCHEMA,
            correlation_id="c", cache_key="ctx3",
        )
    assert provider.calls == 2  # 1 + 1 retry


async def test_cache_hit_makes_zero_provider_calls() -> None:
    provider = FakeProvider([{"direction": "UP", "confidence": 0.7}])
    gw = LLMGateway(_settings(), {"fake": provider})
    kwargs = dict(
        task="t", prompt_version="v1", messages=MESSAGES, output_schema=SCHEMA,
        correlation_id="c", cache_key="ctx4",
    )
    first = await gw.complete_structured(**kwargs)  # type: ignore[arg-type]
    second = await gw.complete_structured(**kwargs)  # type: ignore[arg-type]
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert provider.calls == 1  # second served from cache


async def test_oversized_input_rejected_before_call() -> None:
    provider = FakeProvider([{"direction": "UP", "confidence": 0.7}])
    gw = LLMGateway(_settings(max_input_tokens=1), {"fake": provider})
    with pytest.raises(LLMConfigurationError):
        await gw.complete_structured(
            task="t", prompt_version="v1",
            messages=[{"role": "user", "content": "x" * 1000}],
            output_schema=SCHEMA, correlation_id="c",
        )
    assert provider.calls == 0


def test_missing_provider_key_fails_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = LLMSettings(provider="openai", model="test-model")
    with pytest.raises(LLMConfigurationError, match="API key"):
        build_provider(settings)


def test_api_key_override_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_LLM_KEY", "secret")
    settings = LLMSettings(
        provider="openai",
        model="test-model",
        api_key_env="MY_LLM_KEY",
    )
    assert settings.require_api_key() == "secret"


async def test_invalid_output_schema_is_rejected_before_call() -> None:
    provider = FakeProvider([{"direction": "UP", "confidence": 0.7}])
    gw = LLMGateway(_settings(), {"fake": provider})
    with pytest.raises(LLMConfigurationError, match="invalid output schema"):
        await gw.complete_structured(
            task="t",
            prompt_version="v1",
            messages=MESSAGES,
            output_schema={"type": "not-a-json-schema-type"},
            correlation_id="c",
        )
    assert provider.calls == 0


async def test_json_schema_enum_is_enforced() -> None:
    schema = {
        "type": "object",
        "required": ["direction"],
        "properties": {"direction": {"type": "string", "enum": ["UP", "DOWN"]}},
    }
    provider = FakeProvider([{"direction": "SIDEWAYS"}, {"direction": "UP"}])
    gw = LLMGateway(_settings(), {"fake": provider})
    result = await gw.complete_structured(
        task="t",
        prompt_version="v1",
        messages=MESSAGES,
        output_schema=schema,
        correlation_id="c",
        cache_key="enum",
    )
    assert result.attempt_count == 2
    assert provider.calls == 2


@pytest.mark.integration
async def test_openai_structured_response_live() -> None:
    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("LLM_MODEL"):
        pytest.skip("OPENAI_API_KEY and LLM_MODEL are required for the live provider test")

    settings = LLMSettings(provider="openai")
    provider = build_provider(settings)
    gateway = LLMGateway(settings, {provider.name: provider})
    result = await gateway.complete_structured(
        task="integration-smoke",
        prompt_version="v1",
        messages=[{"role": "user", "content": "Return direction UP and confidence 1."}],
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["direction", "confidence"],
            "properties": {
                "direction": {"type": "string", "enum": ["UP"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
        },
        correlation_id="integration",
        cache_key="integration-smoke-v1",
    )
    assert result.status == "SUCCESS"
    assert result.provider == "openai"
