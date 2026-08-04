from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from shared.llm.gateway import LLMGateway, LLMResult
from shared.reference import members_of
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    EventPolarity,
    EventType,
    ExtractionMethod,
)

from cleansing.exceptions import AmbiguousMergeError
from cleansing.merge import ClusterInputs, LlmMerger, build_local_event, detect_fact_conflicts
from cleansing.models import ClusterRecord, ClusterState


def _record(event_type: EventType) -> ClusterRecord:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return ClusterRecord(
        cluster_id=uuid.uuid4(),
        event_type=event_type,
        state=ClusterState.MERGING,
        article_count=2,
        first_seen_at=now,
        last_seen_at=now,
        quiet_deadline=now,
        lifetime_deadline=now,
        centroid=[1.0],
    )


def _article(title: str, source: str) -> dict[str, Any]:
    return {
        "article_id": uuid.uuid4(),
        "title": title,
        "source_id": source,
        "canonical_url": "https://example.com/a",
        "published_at": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "correlation_id": uuid.uuid4(),
    }


def _action(
    actor: str | None,
    lemma: str,
    assets: list[str],
    *,
    polarity: str = "OCCURRENCE",
    context_tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "article_id": uuid.uuid4(),
        "actor": actor,
        "action_lemma": lemma,
        "object": None,
        "event_type": EventType.SANCTIONS.value,
        "affected_asset_ids": assets,
        "polarity": polarity,
        "context_tags": context_tags or [],
    }


def test_build_local_event_is_deterministic_and_local() -> None:
    record = _record(EventType.SANCTIONS)
    articles: list[Any] = [_article("EU imposes sanctions on exports", "reuters")]
    actions: list[Any] = [_action("EU", "sanction", ["BRENT_OIL"])]
    event = build_local_event(ClusterInputs(record=record, articles=articles, actions=actions))

    assert event.extraction_method == ExtractionMethod.LOCAL
    assert event.llm_metadata is None
    assert event.event_type == EventType.SANCTIONS
    assert event.cluster_id == record.cluster_id
    assert event.canonical_summary == "EU imposes sanctions on exports"
    assert [a.value for a in event.affected_asset_ids] == ["BRENT_OIL"]
    assert len(event.sources) == 1
    assert event.fact_conflicts == []
    # correlation_id is propagated from the first source article.
    assert event.correlation_id == articles[0]["correlation_id"]


def test_no_conflict_for_agreeing_actors() -> None:
    actions: list[Any] = [_action("EU", "sanction", []), _action("EU", "sanction", [])]
    assert detect_fact_conflicts(actions) == []


def test_conflict_for_disagreeing_actors() -> None:
    actions: list[Any] = [_action("EU", "sanction", []), _action("US", "sanction", [])]
    conflicts = detect_fact_conflicts(actions)
    assert len(conflicts) == 1
    assert conflicts[0].field == "actor"
    assert {v.value for v in conflicts[0].values} == {"EU", "US"}


def test_build_local_event_without_articles_uses_record_window() -> None:
    record = _record(EventType.OTHER)
    event = build_local_event(ClusterInputs(record=record, articles=[], actions=[]))
    assert event.extraction_method == ExtractionMethod.LOCAL
    assert event.first_seen_at == record.first_seen_at
    assert event.last_seen_at == record.last_seen_at
    assert event.canonical_summary == EventType.OTHER.value
    assert event.sources == []


def test_build_local_event_defaults_polarity_and_no_tags() -> None:
    record = _record(EventType.SANCTIONS)
    articles: list[Any] = [_article("EU sanctions exports", "reuters")]
    actions: list[Any] = [_action("EU", "sanction", ["BRENT_OIL"])]
    event = build_local_event(ClusterInputs(record=record, articles=articles, actions=actions))
    assert event.polarity == EventPolarity.OCCURRENCE
    assert event.context_tags == []


def test_build_local_event_carries_polarity_and_context_tags() -> None:
    record = _record(EventType.MILITARY_CONFLICT)
    articles: list[Any] = [_article("Strike called off", "reuters")]
    actions: list[Any] = [
        _action("USA", "attack", [], polarity="RESOLUTION", context_tags=["SAFE_HAVEN_ONLY"]),
        _action("USA", "attack", [], polarity="RESOLUTION", context_tags=["SAFE_HAVEN_ONLY"]),
    ]
    event = build_local_event(ClusterInputs(record=record, articles=articles, actions=actions))
    assert event.polarity == EventPolarity.RESOLUTION
    assert event.context_tags == [ConditionCode.SAFE_HAVEN_ONLY]


def test_build_local_event_polarity_needs_strict_majority() -> None:
    record = _record(EventType.MILITARY_CONFLICT)
    actions: list[Any] = [
        _action("USA", "attack", [], polarity="RESOLUTION"),
        _action("USA", "attack", [], polarity="OCCURRENCE"),
    ]
    event = build_local_event(ClusterInputs(record=record, articles=[], actions=actions))
    # A tie is not a majority, so the conservative OCCURRENCE default holds.
    assert event.polarity == EventPolarity.OCCURRENCE


def test_build_local_event_context_tags_are_unioned() -> None:
    record = _record(EventType.MILITARY_CONFLICT)
    actions: list[Any] = [
        _action("USA", "attack", [], context_tags=["TRANSPORT_AFFECTED"]),
        _action("USA", "attack", [], context_tags=["SAFE_HAVEN_ONLY", "TRANSPORT_AFFECTED"]),
    ]
    event = build_local_event(ClusterInputs(record=record, articles=[], actions=actions))
    assert event.context_tags == [
        ConditionCode.TRANSPORT_AFFECTED,
        ConditionCode.SAFE_HAVEN_ONLY,
    ]


def test_build_local_event_asset_fallback_from_event_type() -> None:
    # Geopolitical cluster whose actions named no asset still resolves to its graph assets: the
    # safe-haven commodities plus the weapons makers a conflict moves, in every market.
    record = _record(EventType.MILITARY_CONFLICT)
    actions: list[Any] = [_action("USA", "attack", [])]
    event = build_local_event(ClusterInputs(record=record, articles=[], actions=actions))
    assert {AssetId.GOLD, AssetId.BRENT_OIL} <= set(event.affected_asset_ids)
    assert set(members_of("WEAPON_INDUSTRY")) <= set(event.affected_asset_ids)


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
        self.calls.append({"task": task, "messages": messages})
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _llm_result(content: dict[str, Any]) -> LLMResult:
    return LLMResult(
        content=content,
        provider="fake",
        model="fake-model",
        prompt_version="cleansing-merge-v1",
        context_hash="ctx",
        input_tokens=10,
        output_tokens=5,
        latency_ms=42,
        attempt_count=1,
        cache_hit=False,
        status="SUCCESS",
    )


def _merger(gateway: _FakeGateway) -> LlmMerger:
    return LlmMerger(
        cast(LLMGateway, gateway),
        prompt_version="cleansing-merge-v1",
        max_excerpts=5,
        excerpt_chars=600,
    )


def test_build_messages_bounds_sources_and_guards_injection() -> None:
    gateway = _FakeGateway(result=_llm_result({}))
    merger = _merger(gateway)
    articles: list[Any] = [_article(f"headline {i}", "src") for i in range(7)]
    inputs = ClusterInputs(record=_record(EventType.SANCTIONS), articles=articles, actions=[])
    messages = merger._build_messages(inputs)
    system, user = messages[0]["content"], messages[1]["content"]
    # Only max_excerpts sources are included, and the untrusted-data guard is present.
    assert user.count("[SOURCE ") == 5
    assert "untrusted" in system.lower()
    assert "<SOURCES>" in user


async def test_llm_merge_success_produces_llm_assisted_event() -> None:
    gateway = _FakeGateway(
        result=_llm_result(
            {
                "canonical_summary": "EU and US both sanction exports",
                "actor": "EU",
                "action": "sanction",
                "object": "exports",
            }
        )
    )
    record = _record(EventType.SANCTIONS)
    articles: list[Any] = [_article("EU sanctions", "reuters"), _article("US sanctions", "ap")]
    actions: list[Any] = [_action("EU", "sanction", ["BRENT_OIL"]), _action("US", "sanction", [])]
    event = await _merger(gateway).merge(
        ClusterInputs(record=record, articles=articles, actions=actions)
    )

    assert event.extraction_method == ExtractionMethod.LLM_ASSISTED
    assert event.canonical_summary == "EU and US both sanction exports"
    assert event.actor == "EU"
    assert event.llm_metadata is not None
    assert event.llm_metadata.model == "fake-model"
    # Conflicting source actors are still surfaced on the merged event.
    assert any(c.field == "actor" for c in event.fact_conflicts)
    assert gateway.calls and gateway.calls[0]["task"] == "cleansing_merge"


async def test_llm_merge_blank_actor_normalizes_to_none() -> None:
    gateway = _FakeGateway(
        result=_llm_result(
            {
                "canonical_summary": "something happened",
                "actor": "   ",
                "action": None,
                "object": None,
            }
        )
    )
    articles: list[Any] = [_article("t", "s")]
    event = await _merger(gateway).merge(
        ClusterInputs(record=_record(EventType.OTHER), articles=articles, actions=[])
    )
    assert event.actor is None
    assert event.action is None


async def test_llm_merge_empty_summary_raises_ambiguous() -> None:
    gateway = _FakeGateway(result=_llm_result({"canonical_summary": "   "}))
    articles: list[Any] = [_article("t", "s")]
    with pytest.raises(AmbiguousMergeError, match="empty canonical_summary"):
        await _merger(gateway).merge(
            ClusterInputs(record=_record(EventType.OTHER), articles=articles, actions=[])
        )


async def test_llm_merge_gateway_failure_raises_ambiguous() -> None:
    gateway = _FakeGateway(error=RuntimeError("boom"))
    articles: list[Any] = [_article("t", "s")]
    with pytest.raises(AmbiguousMergeError, match="LLM merge failed"):
        await _merger(gateway).merge(
            ClusterInputs(record=_record(EventType.OTHER), articles=articles, actions=[])
        )
