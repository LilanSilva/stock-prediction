from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from shared.graph.exceptions import GraphTransportError
from shared.schemas.messages import Direction, EventDetected, EventType, Magnitude

from prediction.config import PredictionSettings
from prediction.models import ActivePrediction, ContextEvent, Decision
from prediction.pipeline import PredictionPipeline
from prediction.research import (
    CapturedOpportunity,
    EventEvidence,
    ResearchRecorder,
    build_opportunity,
    canonical_json,
    content_hash,
)

from .test_pipeline import _context, _edge, _event, _FakeGraph, _FakePriceReader, _FakeRepo

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


def evidence_for(event: EventDetected) -> EventEvidence:
    payload = canonical_json(event.model_dump(mode="json", exclude={
        "message_id", "correlation_id", "causation_id", "occurred_at",
    }))
    return EventEvidence(event.event_id, content_hash(payload), payload, NOW - timedelta(days=1))


def context_event(event: EventDetected) -> ContextEvent:
    return ContextEvent(event.event_id, event.event_type, event.first_seen_at,
                        event.polarity, list(event.context_tags))


class MemoryEvidence:
    def __init__(self) -> None:
        self.events: list[EventEvidence] = []
        self.opportunities: dict[uuid.UUID, CapturedOpportunity] = {}

    async def record_event(self, event: EventDetected) -> None:
        item = evidence_for(event)
        if item not in self.events:
            self.events.append(item)

    async def load_events(
        self, event_ids: list[uuid.UUID], cutoff: datetime,
    ) -> list[EventEvidence]:
        return [event for event in self.events if event.event_id in event_ids]

    async def save(self, opportunity: CapturedOpportunity) -> bool:
        if opportunity.opportunity_id in self.opportunities:
            return False
        self.opportunities[opportunity.opportunity_id] = opportunity
        return True


def test_snapshot_freezes_provenance_without_claiming_training_readiness() -> None:
    event = _event()
    edge = _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)
    result = build_opportunity(
        _context(), [context_event(event)], [evidence_for(event)], [edge], False,
        Decision(Direction.NEUTRAL, Magnitude.SMALL, 0.12, "weak evidence"),
        NOW, {"deadband": 0.15},
    )
    snapshot = json.loads(result.snapshot)
    assert result.quality == "VALID"
    assert result.result_status == "PREDICTED"  # NEUTRAL is not abstention.
    assert json.loads(result.result)["direction"] == "NEUTRAL"
    assert json.loads(result.result)["probabilities"] is None
    assert snapshot["training_eligible"] is False
    assert snapshot["market_feature_status"] == "NOT_CAPTURED"
    assert snapshot["news"][0]["payload"]["cluster_id"] == str(event.cluster_id)
    assert snapshot["firing_edges"][0]["weight"] == 0.75
    assert result.snapshot_hash == content_hash(result.snapshot)


@pytest.mark.parametrize("case,reason", [
    ("missing", "MISSING_EVENT_RECEIPT"),
    ("revised", "AMBIGUOUS_EVENT_REVISION"),
    ("late", "FUTURE_EVENT_EVIDENCE"),
    ("hash", "EVENT_HASH_MISMATCH"),
    ("mismatch", "CONTEXT_EVENT_MISMATCH"),
])
def test_invalid_news_is_preserved_as_invalid_not_silently_used(case: str, reason: str) -> None:
    event = _event()
    evidence = evidence_for(event)
    versions = [evidence]
    member = context_event(event)
    if case == "missing":
        versions = []
    elif case == "revised":
        versions.append(evidence_for(event.model_copy(update={"canonical_summary": "revision"})))
    elif case == "late":
        versions = [replace(evidence, received_at=NOW + timedelta(seconds=1))]
    elif case == "hash":
        versions = [replace(evidence, payload="broken JSON", content_hash="bad")]
    elif case == "mismatch":
        member = replace(member, event_type=EventType.CORPORATE_EARNINGS)
    result = build_opportunity(_context(), [member], versions, [], False, None, NOW, {})
    assert result.quality == "INVALID_INPUT"
    assert reason in json.loads(result.snapshot)["quality_reasons"]
    assert json.loads(result.snapshot)["news"] == []


def test_identity_and_snapshot_do_not_depend_on_event_or_edge_iteration_order() -> None:
    events = [_event(), _event(event_type=EventType.CORPORATE_EARNINGS)]
    members = [context_event(event) for event in events]
    versions = [evidence_for(event) for event in events]
    edges = [_edge(event.event_type, Direction.UP, 0.5) for event in events]
    record = _context()
    first = build_opportunity(record, members, versions, edges, False, None, NOW, {})
    second = build_opportunity(record, members[::-1], versions[::-1], edges[::-1], False,
                               None, NOW, {})
    assert first == second


def test_configuration_is_opt_in_and_does_not_offer_unimplemented_model_mode() -> None:
    assert PredictionSettings().research_mode == "OFF"
    with pytest.raises(ValidationError):
        PredictionSettings(research_mode="SHADOW")
    with pytest.raises(ValueError, match="timezone-aware"):
        build_opportunity(_context(), [], [], [], False, None, NOW.replace(tzinfo=None), {})


@pytest.mark.parametrize("scenario", ["abstain", "suppressed", "graph_failure", "emit"])
async def test_capture_precedes_official_filtering_and_keeps_failures(scenario: str) -> None:
    event = _event()
    record = _context()
    store = MemoryEvidence()
    await store.record_event(event)
    settings = PredictionSettings()
    recorder = ResearchRecorder(store, settings)
    repo = _FakeRepo(
        claimed=[record], events={record.context_id: [context_event(event)]},
        active=ActivePrediction(uuid.uuid4(), Direction.UP, Magnitude.LARGE)
        if scenario == "suppressed" else None,
    )
    graph = _FakeGraph(
        edges=[] if scenario == "abstain" else [
            _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75),
        ], error=GraphTransportError("unavailable") if scenario == "graph_failure" else None,
    )
    pipeline = PredictionPipeline(repo, graph, _FakePriceReader(), settings, research=recorder)
    await pipeline.close_ready_contexts(now=NOW)
    assert len(store.opportunities) == 1
    result = next(iter(store.opportunities.values()))
    assert result.result_status == (
        "ABSTAINED" if scenario == "abstain" else "FAILED"
        if scenario == "graph_failure" else "PREDICTED"
    )
    assert len(repo.stored) == (1 if scenario == "emit" else 0)
    assert recorder.failures == 0
    assert recorder.captured == 1
    assert recorder.last_success_at is not None
    # A retry may observe new KG weights, but cannot rewrite the captured first opportunity.
    await pipeline.close_ready_contexts(now=NOW)
    assert recorder.captured == 1
    assert next(iter(store.opportunities.values())) == result


async def test_research_timeout_does_not_block_event_assignment_or_official_prediction() -> None:
    class SlowEvidence(MemoryEvidence):
        async def record_event(self, event: EventDetected) -> None:
            await asyncio.sleep(60)

        async def save(self, opportunity: CapturedOpportunity) -> bool:
            await asyncio.sleep(60)
            return True

    event = _event()
    record = _context()
    settings = PredictionSettings(research_capture_timeout_seconds=0.001)
    recorder = ResearchRecorder(SlowEvidence(), settings)
    repo = _FakeRepo(claimed=[record], events={record.context_id: [context_event(event)]})
    pipeline = PredictionPipeline(
        repo, _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)]),
        _FakePriceReader(), settings, research=recorder,
    )
    await pipeline.process_event(event)
    assert len(repo.assigned) == 1
    assert await pipeline.close_ready_contexts(now=NOW) == 1
    assert recorder.failures == 2


async def test_cancellation_is_not_swallowed() -> None:
    class CancelledEvidence(MemoryEvidence):
        async def record_event(self, event: EventDetected) -> None:
            raise asyncio.CancelledError

    recorder = ResearchRecorder(CancelledEvidence(), PredictionSettings())
    with pytest.raises(asyncio.CancelledError):
        await recorder.record_event(_event())
    assert recorder.failures == 0
