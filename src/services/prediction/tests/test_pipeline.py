from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from shared.graph import CorrelationEdge, FiringEdge
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
        affected_asset_ids=assets if assets is not None else [AssetId.NEM_NYSE],
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
    asset: AssetId = AssetId.NEM_NYSE,
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
        predictions_today: int = 0,
    ) -> None:
        self.assigned: list[dict[str, object]] = []
        self._claimed = claimed or []
        self._events = events or {}
        self._store_result = store_result
        self._predictions_today = predictions_today
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

    async def count_predictions_on(
        self, asset_id: AssetId, local_date: object, timezone_name: str
    ) -> int:
        return self._predictions_today

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
        self,
        *,
        edges: list[FiringEdge] | None = None,
        corr_edges: list[CorrelationEdge] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._edges = edges or []
        self._corr_edges = corr_edges or []
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

    async def get_correlation_edges(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
    ) -> list[CorrelationEdge]:
        return [
            e
            for e in self._corr_edges
            if e.source_asset_id is source_asset_id and e.condition is condition
        ]


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
    repo: _FakeRepo,
    graph: _FakeGraph,
    *,
    elevated: bool = False,
    available: bool = True,
    max_propagation_depth: int = 3,
) -> PredictionPipeline:
    settings = PredictionSettings(max_propagation_depth=max_propagation_depth)
    return PredictionPipeline(
        repo, graph, _FakePriceReader(elevated=elevated, available=available), settings
    )


def _context() -> ContextRecord:
    return ContextRecord(
        context_id=uuid.uuid4(),
        asset_id=AssetId.NEM_NYSE,
        context_version=1,
        window_start=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 27, 15, 0, tzinfo=UTC),
        state=ContextState.PREDICTING,
    )


async def test_process_event_assigns_to_each_affected_asset() -> None:
    repo = _FakeRepo()
    pipeline = _pipeline(repo, _FakeGraph())
    await pipeline.process_event(_event(assets=[AssetId.NEM_NYSE, AssetId.XOM_NYSE]))
    assert {a["asset_id"] for a in repo.assigned} == {AssetId.NEM_NYSE, AssetId.XOM_NYSE}
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
    assert message.asset_id == AssetId.NEM_NYSE
    assert message.event_ids == [events[0].event_id]
    assert key == "NEM_NYSE|2026-07-27T14:00:00+00:00|ONE_TRADING_DAY|1"


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
        asset_id=AssetId.XOM_NYSE,
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
                asset=AssetId.XOM_NYSE,
                condition=ConditionCode.TRANSPORT_AFFECTED,
            ),
            _edge(
                EventType.MILITARY_CONFLICT,
                Direction.UP,
                0.75,
                asset=AssetId.NEM_NYSE,
                condition=ConditionCode.TRANSPORT_AFFECTED,
            ),
            _edge(
                EventType.MILITARY_CONFLICT,
                Direction.UP,
                0.70,
                asset=AssetId.NEM_NYSE,
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
    assert message.asset_id == AssetId.XOM_NYSE
    assert message.direction == Direction.DOWN
    # The contributing edge keeps the seeded triple identity even though its sign was flipped.
    assert (
        message.contributing_edges[0].edge_id
        == "MILITARY_CONFLICT|TRANSPORT_AFFECTED->XOM_NYSE"
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
    assert message.asset_id == AssetId.XOM_NYSE
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
    assert message.asset_id == AssetId.XOM_NYSE
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
    # Same direction as the active stance on a trading day -> do nothing.
    assert produced == 0
    assert repo.stored == []
    assert repo.states[ctx.context_id] == ContextState.PREDICTED


async def test_open_market_skips_magnitude_only_change() -> None:
    # A change of degree is not a change of stance: only a direction flip earns a second prediction.
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.UP, Magnitude.SMALL)
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events}, active=active)
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKDAY)
    assert produced == 0
    assert repo.stored == []


async def test_daily_limit_blocks_a_third_prediction() -> None:
    # Two opposing stances is the day's allowance; anything beyond it is churn. One asset previously
    # accumulated 52 predictions in a single day, flip-flopping UP/DOWN.
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.DOWN, Magnitude.LARGE)
    repo = _FakeRepo(
        claimed=[ctx],
        events={ctx.context_id: events},
        active=active,
        predictions_today=2,
    )
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKDAY)
    assert produced == 0
    assert repo.stored == []
    assert repo.states[ctx.context_id] == ContextState.PREDICTED


async def test_daily_limit_does_not_block_the_second_prediction() -> None:
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.DOWN, Magnitude.LARGE)
    repo = _FakeRepo(
        claimed=[ctx],
        events={ctx.context_id: events},
        active=active,
        predictions_today=1,
    )
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKDAY)
    assert produced == 1
    assert repo.stored[0][0].direction == Direction.UP


async def test_daily_limit_does_not_apply_when_market_closed() -> None:
    # A non-trading day collapses to one active stance by superseding, so the cap is irrelevant
    # there — and must not block the collapse.
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    active = ActivePrediction(uuid.uuid4(), Direction.DOWN, Magnitude.LARGE)
    repo = _FakeRepo(
        claimed=[ctx],
        events={ctx.context_id: events},
        active=active,
        predictions_today=9,
    )
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75)])
    produced = await _pipeline(repo, graph).close_ready_contexts(now=_WEEKEND)
    assert produced == 1
    assert repo.stored[0][0].supersedes_prediction_id == active.prediction_id
    assert repo.withdrawals == [True]


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
    assert await pipeline._is_market_open(AssetId.NEM_NYSE, friday_late_utc) is True


async def test_market_open_is_false_for_an_unregistered_asset() -> None:
    # No registry entry means no calendar; the conservative collapse-to-one path is chosen rather
    # than guessing a market.
    pipeline = _pipeline(_FakeRepo(), _FakeGraph())
    bogus = cast(AssetId, "NOT_IN_REGISTRY")
    assert await pipeline._is_market_open(bogus, datetime(2026, 7, 15, 12, 0, tzinfo=UTC)) is False


# --- cross-asset propagation (E10) -----------------------------------------------------------


def _corr_edge(
    source: AssetId,
    target: AssetId,
    direction: Direction,
    *,
    condition: ConditionCode = ConditionCode.UPSTREAM_UP,
    weight: float = 0.5,
) -> CorrelationEdge:
    return CorrelationEdge(
        source_asset_id=source,
        target_asset_id=target,
        condition=condition,
        direction=direction,
        weight=weight,
        confidence=0.7,
        alpha=2.0,
        beta=1.0,
    )


async def test_propagation_produces_downstream_prediction() -> None:
    # XOM_NYSE is the direct asset; NEM_NYSE has a CORRELATES_WITH edge (DOWN when upstream UP).
    ctx = ContextRecord(
        context_id=uuid.uuid4(),
        asset_id=AssetId.XOM_NYSE,
        context_version=1,
        window_start=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 27, 15, 0, tzinfo=UTC),
        state=ContextState.PREDICTING,
    )
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, asset=AssetId.XOM_NYSE)],
        corr_edges=[_corr_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN)],
    )
    produced = await _pipeline(repo, graph).close_ready_contexts()
    # direct + 1 propagated
    assert produced == 2
    stored_assets = {msg.asset_id for msg, _ in repo.stored}
    assert AssetId.XOM_NYSE in stored_assets
    assert AssetId.NEM_NYSE in stored_assets
    # The propagated message carries depth > 0 and a non-empty chain.
    prop_msg = next(msg for msg, _ in repo.stored if msg.asset_id is AssetId.NEM_NYSE)
    assert prop_msg.propagation_depth == 1
    assert len(prop_msg.propagation_chain) == 1
    hop = prop_msg.propagation_chain[0]
    assert hop.source_asset_id is AssetId.XOM_NYSE
    assert hop.target_asset_id is AssetId.NEM_NYSE
    assert hop.condition is ConditionCode.UPSTREAM_UP


async def test_propagation_visited_set_prevents_cycle() -> None:
    # A ↔ B: if A predicts UP -> B should predict DOWN, but if B also has A as a corr edge
    # the visited set must prevent A from being predicted again.
    ctx = ContextRecord(
        context_id=uuid.uuid4(),
        asset_id=AssetId.XOM_NYSE,
        context_version=1,
        window_start=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 27, 15, 0, tzinfo=UTC),
        state=ContextState.PREDICTING,
    )
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, asset=AssetId.XOM_NYSE)],
        corr_edges=[
            _corr_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN),
            # Back-edge: NEM_NYSE -> XOM_NYSE. XOM_NYSE is already in visited, must be skipped.
            _corr_edge(
                AssetId.NEM_NYSE,
                AssetId.XOM_NYSE,
                Direction.DOWN,
                condition=ConditionCode.UPSTREAM_DOWN,
            ),
        ],
    )
    await _pipeline(repo, graph).close_ready_contexts()
    # Only XOM (direct) and NEM (1 hop) should be stored — XOM must not appear twice.
    stored_assets = [msg.asset_id for msg, _ in repo.stored]
    assert stored_assets.count(AssetId.XOM_NYSE) == 1


async def test_propagation_depth_cap_stops_at_max() -> None:
    # Chain: XOM -> NEM -> LUG_STO.  With max_propagation_depth=1 only the first hop fires.
    ctx = ContextRecord(
        context_id=uuid.uuid4(),
        asset_id=AssetId.XOM_NYSE,
        context_version=1,
        window_start=datetime(2026, 7, 27, 14, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 27, 15, 0, tzinfo=UTC),
        state=ContextState.PREDICTING,
    )
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, asset=AssetId.XOM_NYSE)],
        corr_edges=[
            _corr_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN),
            _corr_edge(
                AssetId.NEM_NYSE,
                AssetId.LUG_STO,
                Direction.DOWN,
                condition=ConditionCode.UPSTREAM_DOWN,
            ),
        ],
    )
    produced = await _pipeline(repo, graph, max_propagation_depth=1).close_ready_contexts()
    stored_assets = {msg.asset_id for msg, _ in repo.stored}
    # NEM should be present (hop 1), LUG should NOT (hop 2 exceeds depth 1).
    assert AssetId.NEM_NYSE in stored_assets
    assert AssetId.LUG_STO not in stored_assets
    # Total = 1 direct + 1 propagated
    assert produced == 2


async def test_no_direct_prediction_skips_propagation() -> None:
    # If the direct context yields no firing edges, no propagation should happen either.
    ctx = _context()
    events = [ContextEvent(uuid.uuid4(), EventType.CORPORATE_EARNINGS, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[],
        corr_edges=[_corr_edge(AssetId.NEM_NYSE, AssetId.XOM_NYSE, Direction.UP)],
    )
    produced = await _pipeline(repo, graph).close_ready_contexts()
    assert produced == 0
    assert repo.stored == []

async def test_converging_edges_at_same_depth_sum_forces() -> None:
    # Force summation across converging correlation edges (ADR-008). XOM fans out to NEM and
    # LUG at depth 1; both then point at SWED_A_STO at depth 2, with OPPOSING directions.
    # Because both arrive in the same depth level they are collected first and decided together,
    # so the heavier edge determines the net direction rather than whichever was reached first.
    ctx = _oil_context()  # direct asset = XOM_NYSE
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, asset=AssetId.XOM_NYSE)],
        corr_edges=[
            # Depth 1: XOM -> NEM (DOWN) and XOM -> LUG (DOWN).
            _corr_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN, weight=0.45),
            _corr_edge(AssetId.XOM_NYSE, AssetId.LUG_STO, Direction.DOWN, weight=0.35),
            # Depth 2: both NEM and LUG point at SWED_A_STO, in opposite directions.
            # NEM's edge is much heavier, so the net must be UP.
            _corr_edge(
                AssetId.NEM_NYSE,
                AssetId.SWED_A_STO,
                Direction.UP,
                condition=ConditionCode.UPSTREAM_DOWN,
                weight=0.90,
            ),
            _corr_edge(
                AssetId.LUG_STO,
                AssetId.SWED_A_STO,
                Direction.DOWN,
                condition=ConditionCode.UPSTREAM_DOWN,
                weight=0.20,
            ),
        ],
    )
    await _pipeline(repo, graph).close_ready_contexts()

    swed = [msg for msg, _ in repo.stored if msg.asset_id is AssetId.SWED_A_STO]
    assert len(swed) == 1, "the converging target must be decided exactly once"
    # Net of +0.90 and -0.20 is UP. Under the old first-wins behaviour this depended purely on
    # dict ordering, and a DOWN result was equally likely.
    assert swed[0].direction is Direction.UP
    assert swed[0].propagation_depth == 2
    # Provenance records BOTH contributing edges, not just one.
    final_hops = [h for h in swed[0].propagation_chain if h.target_asset_id is AssetId.SWED_A_STO]
    assert {h.source_asset_id for h in final_hops} == {AssetId.NEM_NYSE, AssetId.LUG_STO}
    # Both contributing edges are reported for explainability, with distinct edge ids.
    edge_ids = {e.edge_id for e in swed[0].contributing_edges}
    assert edge_ids == {
        "NEM_NYSE|UPSTREAM_DOWN->SWED_A_STO",
        "LUG_STO|UPSTREAM_DOWN->SWED_A_STO",
    }


async def test_converging_edges_cancelling_below_deadband_produce_no_prediction() -> None:
    # Two equal-and-opposite converging forces cancel: net ratio 0 is inside the deadband, so no
    # downstream prediction is emitted at all. Under first-wins one would have been.
    ctx = _oil_context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, asset=AssetId.XOM_NYSE)],
        corr_edges=[
            _corr_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN, weight=0.45),
            _corr_edge(AssetId.XOM_NYSE, AssetId.LUG_STO, Direction.DOWN, weight=0.35),
            # Identical weight, opposing directions -> net 0.
            _corr_edge(
                AssetId.NEM_NYSE,
                AssetId.SWED_A_STO,
                Direction.UP,
                condition=ConditionCode.UPSTREAM_DOWN,
                weight=0.50,
            ),
            _corr_edge(
                AssetId.LUG_STO,
                AssetId.SWED_A_STO,
                Direction.DOWN,
                condition=ConditionCode.UPSTREAM_DOWN,
                weight=0.50,
            ),
        ],
    )
    await _pipeline(repo, graph).close_ready_contexts()
    assert not [msg for msg, _ in repo.stored if msg.asset_id is AssetId.SWED_A_STO]


async def test_first_wins_still_applies_across_different_depths() -> None:
    # Summation is scoped to ONE depth level. A target decided at depth 1 is marked visited, so a
    # heavier edge arriving at depth 2 cannot revise it -- that is what the cycle guard requires.
    ctx = _oil_context()
    events = [ContextEvent(uuid.uuid4(), EventType.MILITARY_CONFLICT, _NOW)]
    repo = _FakeRepo(claimed=[ctx], events={ctx.context_id: events})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.75, asset=AssetId.XOM_NYSE)],
        corr_edges=[
            _corr_edge(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN, weight=0.45),
            # Reaches LUG at depth 1 and wins, despite being the lighter edge.
            _corr_edge(AssetId.XOM_NYSE, AssetId.LUG_STO, Direction.DOWN, weight=0.35),
            _corr_edge(
                AssetId.NEM_NYSE,
                AssetId.LUG_STO,
                Direction.UP,
                condition=ConditionCode.UPSTREAM_DOWN,
                weight=0.90,
            ),
        ],
    )
    await _pipeline(repo, graph).close_ready_contexts()

    lug = [msg for msg, _ in repo.stored if msg.asset_id is AssetId.LUG_STO]
    assert len(lug) == 1
    assert lug[0].direction is Direction.DOWN
    assert lug[0].propagation_depth == 1
