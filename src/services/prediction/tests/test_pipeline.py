from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from shared.graph import FiringEdge
from shared.graph.exceptions import GraphTransportError
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    DecisionMethod,
    Direction,
    EventDetected,
    EventPolarity,
    EventType,
    ExtractionMethod,
    Magnitude,
    PredictionMade,
)

from prediction.config import PredictionSettings
from prediction.models import ActivePrediction, ContextEvent, ContextRecord, ContextState
from prediction.pipeline import PredictionPipeline

_NOW = datetime(2026, 7, 27, 14, 30, tzinfo=UTC)


def _event(
    *,
    event_type: EventType = EventType.MILITARY_CONFLICT,
    assets: list[AssetId] | None = None,
    polarity: EventPolarity = EventPolarity.OCCURRENCE,
    context_tags: list[ConditionCode] | None = None,
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
        polarity=polarity,
        context_tags=context_tags if context_tags is not None else [],
    )


def _edge(
    factor: EventType,
    direction: Direction,
    weight: float,
    *,
    asset: AssetId = AssetId.GOLD,
    condition: ConditionCode | None = None,
) -> FiringEdge:
    return FiringEdge(
        factor_id=factor,
        asset_id=asset,
        direction=direction,
        weight=weight,
        confidence=0.7,
        alpha=1.0,
        beta=1.0,
        condition=condition,
    )


class _FakeRepo:
    def __init__(
        self,
        *,
        claimed: list[ContextRecord] | None = None,
        events: dict[uuid.UUID, list[ContextEvent]] | None = None,
        store_result: bool = True,
        active: ActivePrediction | None = None,
    ) -> None:
        self.assigned: list[dict[str, object]] = []
        self._claimed = claimed or []
        self._events = events or {}
        self._store_result = store_result
        self._active = active
        self.states: dict[uuid.UUID, ContextState] = {}
        self.stored: list[tuple[PredictionMade, str]] = []
        self.withdrawals: list[bool] = []

    async def assign_event(self, **kwargs: object) -> None:
        self.assigned.append(kwargs)

    async def claim_ready_contexts(
        self, now: datetime, *, grace_minutes: int, limit: int = 20
    ) -> list[ContextRecord]:
        return self._claimed

    async def load_context_events(self, context_id: uuid.UUID) -> list[ContextEvent]:
        return self._events.get(context_id, [])

    async def latest_active_prediction(self, asset_id: AssetId) -> ActivePrediction | None:
        return self._active

    async def set_context_state(self, context_id: uuid.UUID, state: ContextState) -> None:
        self.states[context_id] = state

    async def store_prediction_with_outbox(
        self, message: PredictionMade, *, idempotency_key: str, withdraw_superseded: bool = False
    ) -> bool:
        self.stored.append((message, idempotency_key))
        self.withdrawals.append(withdraw_superseded)
        return self._store_result


class _FakeGraph:
    def __init__(
        self, *, edges: list[FiringEdge] | None = None, error: Exception | None = None
    ) -> None:
        self._edges = edges or []
        self._error = error
        self.calls: list[tuple[EventType, set[ConditionCode] | None]] = []

    async def get_firing_edges(
        self,
        event_type: EventType,
        asset_ids: list[AssetId] | None = None,
        conditions: set[ConditionCode] | None = None,
    ) -> list[FiringEdge]:
        if self._error is not None:
            raise self._error
        self.calls.append((event_type, conditions))
        matched: list[FiringEdge] = []
        for edge in self._edges:
            if edge.factor_id != event_type:
                continue
            if asset_ids is not None and edge.asset_id not in asset_ids:
                continue
            # Unconditional edges always fire; conditioned edges fire only when active.
            if edge.condition is not None and (
                conditions is not None and edge.condition not in conditions
            ):
                continue
            matched.append(edge)
        return matched


class _FakePriceReader:
    def __init__(self, *, elevated: bool = False, available: bool = True) -> None:
        self._elevated = elevated
        self._available = available
        self.calls: list[AssetId] = []

    async def is_elevated(self, asset_id: AssetId) -> bool:
        self.calls.append(asset_id)
        return self._elevated

    async def is_price_available(self, asset_id: AssetId) -> bool:
        return self._available


def _pipeline(
    repo: _FakeRepo, graph: _FakeGraph, *, elevated: bool = False, available: bool = True
) -> PredictionPipeline:
    return PredictionPipeline(
        repo, graph, _FakePriceReader(elevated=elevated, available=available), PredictionSettings()
    )


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
    expected_window = datetime(2026, 7, 27, 14, 30, tzinfo=UTC)
    assert all(a["window_start"] == expected_window for a in repo.assigned)


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


def _oil_context() -> ContextRecord:
    return ContextRecord(
        context_id=uuid.uuid4(),
        asset_id=AssetId.BRENT_OIL,
        context_version=1,
        window_start=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 27, 15, 0, tzinfo=UTC),
        state=ContextState.PREDICTING,
    )


def _conditioned_conflict_graph() -> _FakeGraph:
    # Mirrors the seeded conditioned edges for MILITARY_CONFLICT.
    return _FakeGraph(
        edges=[
            _edge(
                EventType.MILITARY_CONFLICT,
                Direction.UP,
                0.65,
                asset=AssetId.BRENT_OIL,
                condition=ConditionCode.TRANSPORT_AFFECTED,
            ),
            _edge(
                EventType.MILITARY_CONFLICT,
                Direction.UP,
                0.75,
                asset=AssetId.GOLD,
                condition=ConditionCode.TRANSPORT_AFFECTED,
            ),
            _edge(
                EventType.MILITARY_CONFLICT,
                Direction.UP,
                0.70,
                asset=AssetId.GOLD,
                condition=ConditionCode.SAFE_HAVEN_ONLY,
            ),
        ]
    )


async def test_process_event_persists_polarity_and_context_tags() -> None:
    repo = _FakeRepo()
    event = _event(
        polarity=EventPolarity.RESOLUTION,
        context_tags=[ConditionCode.TRANSPORT_AFFECTED],
    )
    await _pipeline(repo, _FakeGraph()).process_event(event)
    assigned = repo.assigned[0]
    assert assigned["polarity"] == EventPolarity.RESOLUTION
    assert assigned["context_tags"] == [ConditionCode.TRANSPORT_AFFECTED]


async def test_transport_affected_resolution_flips_oil_up_to_down() -> None:
    ctx = _oil_context()
    events = [
        ContextEvent(
            uuid.uuid4(),
            EventType.MILITARY_CONFLICT,
            _NOW,
            polarity=EventPolarity.RESOLUTION,
            context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        )
    ]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    # An elevated price means there is a risk premium to unwind, so the flip to DOWN applies.
    produced = await _pipeline(
        repo, _conditioned_conflict_graph(), elevated=True
    ).close_ready_contexts()
    assert produced == 1
    message, _ = repo.stored[0]
    assert message.asset_id == AssetId.BRENT_OIL
    assert message.direction == Direction.DOWN
    # The contributing edge keeps the seeded triple identity even though its sign was flipped.
    assert (
        message.contributing_edges[0].edge_id
        == "MILITARY_CONFLICT|TRANSPORT_AFFECTED->BRENT_OIL"
    )


async def test_resolution_down_suppressed_when_price_not_elevated() -> None:
    ctx = _oil_context()
    events = [
        ContextEvent(
            uuid.uuid4(),
            EventType.MILITARY_CONFLICT,
            _NOW,
            polarity=EventPolarity.RESOLUTION,
            context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        )
    ]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    # Flat price -> nothing to revert -> the RESOLUTION-driven DOWN is dropped, no prediction.
    produced = await _pipeline(
        repo, _conditioned_conflict_graph(), elevated=False
    ).close_ready_contexts()
    assert produced == 0
    assert repo.stored == []
    assert repo.states[ctx.context_id] == ContextState.PREDICTED


async def test_transport_affected_occurrence_keeps_oil_up() -> None:
    ctx = _oil_context()
    events = [
        ContextEvent(
            uuid.uuid4(),
            EventType.MILITARY_CONFLICT,
            _NOW,
            polarity=EventPolarity.OCCURRENCE,
            context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        )
    ]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    produced = await _pipeline(repo, _conditioned_conflict_graph()).close_ready_contexts()
    assert produced == 1
    message, _ = repo.stored[0]
    assert message.asset_id == AssetId.BRENT_OIL
    assert message.direction == Direction.UP


async def test_occurrence_conflict_unaffected_by_elevated_price() -> None:
    ctx = _oil_context()
    events = [
        ContextEvent(
            uuid.uuid4(),
            EventType.MILITARY_CONFLICT,
            _NOW,
            polarity=EventPolarity.OCCURRENCE,
            context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        )
    ]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    # An OCCURRENCE conflict lifts oil UP regardless of whether the price gate reports elevation.
    produced = await _pipeline(
        repo, _conditioned_conflict_graph(), elevated=True
    ).close_ready_contexts()
    assert produced == 1
    message, _ = repo.stored[0]
    assert message.asset_id == AssetId.BRENT_OIL
    assert message.direction == Direction.UP
    ctx = _oil_context()
    events = [
        ContextEvent(
            uuid.uuid4(),
            EventType.MILITARY_CONFLICT,
            _NOW,
            context_tags=[ConditionCode.SAFE_HAVEN_ONLY],
        )
    ]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    produced = await _pipeline(repo, _conditioned_conflict_graph()).close_ready_contexts()
    # Oil has only a TRANSPORT_AFFECTED edge, so safe-haven-only conflict yields no oil prediction.
    assert produced == 0
    assert repo.stored == []
    assert repo.states[ctx.context_id] == ContextState.PREDICTED


_WEEKDAY = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)  # Wednesday
_WEEKEND = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)  # Saturday


async def test_open_market_skips_duplicate_signal() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.UP, Magnitude.LARGE)
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events}, active=active)
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKDAY)
    # Same (direction, magnitude) as the active stance on a trading day -> do nothing.
    assert produced == 0
    assert repo.stored == []
    assert repo.states[ctx.context_id] == ContextState.PREDICTED


async def test_open_market_adds_new_prediction_on_change() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.DOWN, Magnitude.LARGE)
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events}, active=active)
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKDAY)
    # Direction changed on a trading day -> add a new independent prediction, no supersede/withdraw.
    assert produced == 1
    message, _ = repo.stored[0]
    assert message.direction == Direction.UP
    assert message.supersedes_prediction_id is None
    assert repo.withdrawals == [False]


async def test_closed_market_collapses_and_withdraws() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.UP, Magnitude.LARGE)
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events}, active=active)
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKEND)
    # Market closed -> collapse to one: supersede + withdraw the prior stance, ignore direction.
    assert produced == 1
    message, _ = repo.stored[0]
    assert message.supersedes_prediction_id == active.prediction_id
    assert repo.withdrawals == [True]


# --- per-market trading calendar (S3) -----------------------------------------------------------


async def test_market_open_uses_the_asset_own_market_calendar() -> None:
    # A Stockholm listing and a US listing can disagree about whether "now" is a trading day, so the
    # stance decision must consult each asset's own calendar rather than New York's.
    pipeline = _pipeline(_FakeRepo(), _FakeGraph())
    friday_late_utc = datetime(2026, 7, 17, 23, 0, tzinfo=UTC)
    # 23:00 UTC Friday is already Saturday 01:00 in Stockholm -> not a trading day there.
    assert await pipeline._is_market_open(AssetId.SAAB_B_STO, friday_late_utc) is False
    # The same instant is still Friday evening in New York -> a trading day.
    assert await pipeline._is_market_open(AssetId.GOLD, friday_late_utc) is True


async def test_market_open_is_false_for_an_unregistered_asset() -> None:
    # No registry entry means no calendar; the conservative collapse-to-one path is chosen rather
    # than guessing a market.
    pipeline = _pipeline(_FakeRepo(), _FakeGraph())
    bogus = cast(AssetId, "NOT_IN_REGISTRY")
    assert await pipeline._is_market_open(bogus, datetime(2026, 7, 15, 12, 0, tzinfo=UTC)) is False
