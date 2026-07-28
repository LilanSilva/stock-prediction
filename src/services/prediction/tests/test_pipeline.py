from __future__ import annotations

import uuid
from datetime import UTC, datetime

from shared.graph import FiringEdge
from shared.graph.exceptions import GraphTransportError
from shared.schemas.messages import (
    AssetId,
    DecisionMethod,
    Direction,
    EventDetected,
    EventType,
    ExtractionMethod,
    PredictionMade,
)

from prediction.config import PredictionSettings
from prediction.models import ContextEvent, ContextRecord, ContextState
from prediction.pipeline import PredictionPipeline

_NOW = datetime(2026, 7, 27, 14, 30, tzinfo=UTC)


def _event(
    *,
    event_type: EventType = EventType.MILITARY_CONFLICT,
    assets: list[AssetId] | None = None,
) -> EventDetected:
    return EventDetected(
        correlation_id=uuid.uuid4(),
        occurred_at=_NOW,
        event_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        canonical_summary="something happened",
        event_type=event_type,
        affected_asset_ids=assets if assets is not None else [AssetId.GOLD],
        first_seen_at=_NOW,
        last_seen_at=_NOW,
        extraction_method=ExtractionMethod.LOCAL,
    )


def _edge(factor: EventType, direction: Direction, weight: float) -> FiringEdge:
    return FiringEdge(
        factor_id=factor,
        asset_id=AssetId.GOLD,
        direction=direction,
        weight=weight,
        confidence=0.7,
        alpha=1.0,
        beta=1.0,
    )


class _FakeRepo:
    def __init__(
        self,
        *,
        claimed: list[ContextRecord] | None = None,
        events: dict[uuid.UUID, list[ContextEvent]] | None = None,
        store_result: bool = True,
    ) -> None:
        self.assigned: list[dict[str, object]] = []
        self._claimed = claimed or []
        self._events = events or {}
        self._store_result = store_result
        self.states: dict[uuid.UUID, ContextState] = {}
        self.stored: list[tuple[PredictionMade, str]] = []

    async def assign_event(self, **kwargs: object) -> None:
        self.assigned.append(kwargs)

    async def claim_ready_contexts(
        self, now: datetime, *, grace_minutes: int, limit: int = 20
    ) -> list[ContextRecord]:
        return self._claimed

    async def load_context_events(self, context_id: uuid.UUID) -> list[ContextEvent]:
        return self._events.get(context_id, [])

    async def latest_prediction_id(
        self, asset_id: AssetId, window_start: datetime
    ) -> uuid.UUID | None:
        return None

    async def set_context_state(self, context_id: uuid.UUID, state: ContextState) -> None:
        self.states[context_id] = state

    async def store_prediction_with_outbox(
        self, message: PredictionMade, *, idempotency_key: str
    ) -> bool:
        self.stored.append((message, idempotency_key))
        return self._store_result


class _FakeGraph:
    def __init__(
        self, *, edges: list[FiringEdge] | None = None, error: Exception | None = None
    ) -> None:
        self._edges = edges or []
        self._error = error

    async def get_firing_edges(
        self, event_type: EventType, asset_ids: list[AssetId] | None = None
    ) -> list[FiringEdge]:
        if self._error is not None:
            raise self._error
        return [e for e in self._edges if e.factor_id == event_type]


def _pipeline(repo: _FakeRepo, graph: _FakeGraph) -> PredictionPipeline:
    return PredictionPipeline(repo, graph, PredictionSettings())


def _context() -> ContextRecord:
    return ContextRecord(
        context_id=uuid.uuid4(),
        asset_id=AssetId.GOLD,
        context_version=1,
        window_start=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 27, 15, 0, tzinfo=UTC),
        state=ContextState.PREDICTING,
    )


async def test_process_event_assigns_to_each_affected_asset() -> None:
    repo = _FakeRepo()
    pipeline = _pipeline(repo, _FakeGraph())
    await pipeline.process_event(_event(assets=[AssetId.GOLD, AssetId.BRENT_OIL]))
    assert {a["asset_id"] for a in repo.assigned} == {AssetId.GOLD, AssetId.BRENT_OIL}
    assert all(a["window_start"] == datetime(2026, 7, 27, 14, 0, tzinfo=UTC) for a in repo.assigned)


async def test_process_event_without_assets_is_a_noop() -> None:
    repo = _FakeRepo()
    await _pipeline(repo, _FakeGraph()).process_event(_event(assets=[]))
    assert repo.assigned == []


async def test_close_produces_graph_only_prediction() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts()
    assert produced == 1
    message, key = repo.stored[0]
    assert message.decision_method == DecisionMethod.GRAPH_ONLY
    assert message.llm_metadata is None
    assert message.direction == Direction.UP
    assert message.asset_id == AssetId.GOLD
    assert message.event_ids == [events[0].event_id]
    assert key == "GOLD|2026-07-27T14:00:00+00:00|ONE_TRADING_DAY|1"


async def test_close_with_no_firing_edges_makes_no_prediction() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.CORPORATE_EARNINGS, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    produced = await _pipeline(repo, _FakeGraph(edges=[])).close_ready_contexts()
    assert produced == 0
    assert repo.states[ctx.context_id] == ContextState.PREDICTED
    assert repo.stored == []


async def test_close_graph_error_marks_retryable() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(error=GraphTransportError("bolt down"))
    produced = await _pipeline(repo, graph).close_ready_contexts()
    assert produced == 0
    assert repo.states[ctx.context_id] == ContextState.ERROR_RETRYABLE


async def test_close_is_idempotent_when_store_reports_duplicate() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events}, store_result=False)
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts()
    # A duplicate context version is stored-but-not-counted (no second identity).
    assert produced == 0
    assert len(repo.stored) == 1
