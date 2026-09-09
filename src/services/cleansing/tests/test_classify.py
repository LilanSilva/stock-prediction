from __future__ import annotations

from typing import Any, cast

from shared.llm.gateway import LLMGateway, LLMResult
from shared.schemas.messages import EventType

from cleansing.classify import CLASSIFY_OUTPUT_SCHEMA, LlmClassifier


class _FakeGateway:
    """Structural stand-in for LLMGateway.complete_structured (no network)."""

    def __init__(self, *, result: LLMResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"task": task, "messages": messages, "output_schema": output_schema})
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _llm_result(content: dict[str, Any]) -> LLMResult:
    return LLMResult(
        content=content,
        provider="fake",
        model="fake-model",
        prompt_version="cleansing-classify-v1",
        context_hash="ctx",
        input_tokens=10,
        output_tokens=5,
        latency_ms=42,
        attempt_count=1,
        cache_hit=False,
        status="SUCCESS",
    )


def _classifier(gateway: _FakeGateway) -> LlmClassifier:
    return LlmClassifier(
        cast(LLMGateway, gateway),
        prompt_version="cleansing-classify-v1",
        body_chars=600,
    )


async def test_classify_returns_the_llm_chosen_type() -> None:
    gateway = _FakeGateway(result=_llm_result({"event_type": "LEGAL_DISPUTE"}))
    event_type = await _classifier(gateway).classify(
        "Rättegången inledd", "En rättegång har inletts.", "https://example.com/a"
    )
    assert event_type is EventType.LEGAL_DISPUTE
    assert gateway.calls and gateway.calls[0]["task"] == "cleansing_classify"


async def test_classify_messages_carry_the_untrusted_data_guard() -> None:
    gateway = _FakeGateway(result=_llm_result({"event_type": "OTHER"}))
    await _classifier(gateway).classify("A headline", "A body", "https://example.com/a")
    system, user = gateway.calls[0]["messages"][0]["content"], gateway.calls[0]["messages"][1]["content"]
    assert "untrusted" in system.lower()
    assert "<ARTICLE>" in user
    assert "A headline" in user


async def test_classify_truncates_the_body() -> None:
    gateway = _FakeGateway(result=_llm_result({"event_type": "OTHER"}))
    classifier = LlmClassifier(cast(LLMGateway, gateway), prompt_version="v1", body_chars=10)
    await classifier.classify("t", "0123456789ABCDEF", "")
    user = gateway.calls[0]["messages"][1]["content"]
    assert "0123456789ABCDEF" not in user
    assert "0123456789" in user


async def test_classify_output_schema_only_allows_registry_values() -> None:
    enum = CLASSIFY_OUTPUT_SCHEMA["properties"]["event_type"]["enum"]  # type: ignore[index]
    assert "MILITARY_CONFLICT" in enum
    assert "OTHER" in enum
    assert "NOT_A_REAL_TYPE" not in enum


async def test_classify_invalid_event_type_degrades_to_other() -> None:
    gateway = _FakeGateway(result=_llm_result({"event_type": "NOT_A_REAL_TYPE"}))
    event_type = await _classifier(gateway).classify("t", "", "")
    assert event_type is EventType.OTHER


async def test_classify_gateway_failure_degrades_to_other_without_raising() -> None:
    gateway = _FakeGateway(error=RuntimeError("boom"))
    event_type = await _classifier(gateway).classify("t", "", "")
    assert event_type is EventType.OTHER
