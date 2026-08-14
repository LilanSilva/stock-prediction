"""Tests for the E12 S03 propagation gate and prediction provenance.

Reuses the fakes from ``test_pipeline`` rather than re-declaring them, so the pipeline under test is
wired exactly as it is there.

The three rules pinned here all come from the 2026-08-12 audit
(backlog/E12-Prediction-Quality-Remediation):

  * PRD-60 — propagation needs a confident source. 39 of 113 predictions (35%) were purely
  propagated,
    at a 32% hit rate against 47% for direct ones.
  * PRD-61 — a propagated stance never overturns a standing one. NEM_NYSE held 16 UP and 13 DOWN
  from
    the same news, because one event reached both ends of an anti-correlated pair.
  * PRD-62 — a prediction records the events that drove it, not every event in its 15-minute window.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from shared.graph import CorrelationEdge
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    Direction,
    EventType,
    Magnitude,
)

from prediction.config import PredictionSettings
from prediction.models import ActivePrediction, ContextEvent, ContextRecord, ContextState
from prediction.pipeline import PredictionPipeline
from tests.test_pipeline import _edge, _FakeGraph, _FakePriceReader, _FakeRepo

_NOW = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)


def _context(asset: AssetId, context_id: uuid.UUID | None = None) -> ContextRecord:
    return ContextRecord(
        context_id=context_id or uuid.uuid4(),
        asset_id=asset,
        context_version=1,
        window_start=_NOW,
        window_end=_NOW,
        state=ContextState.READY,
    )


def _ctx_event(
    event_type: EventType = EventType.MILITARY_CONFLICT,
    event_id: uuid.UUID | None = None,
) -> ContextEvent:
    return ContextEvent(
        event_id=event_id or uuid.uuid4(),
        event_type=event_type,
        first_seen_at=_NOW,
    )


def _pipeline(
    repo: _FakeRepo,
    graph: _FakeGraph,
    *,
    propagation_min_confidence: float = 0.30,
) -> PredictionPipeline:
    settings = PredictionSettings(propagation_min_confidence=propagation_min_confidence)
    return PredictionPipeline(repo, graph, _FakePriceReader(), settings)


def _correlation(source: AssetId, target: AssetId, direction: Direction) -> CorrelationEdge:
    return CorrelationEdge(
        source_asset_id=source,
        target_asset_id=target,
        direction=direction,
        weight=0.45,
        confidence=0.6,
        alpha=1.0,
        beta=1.0,
        condition=ConditionCode.UPSTREAM_UP,
    )


# --- PRD-60: propagation needs a confident source ------------------------------------------------


async def test_weak_source_does_not_propagate() -> None:
    # A single edge at w=0.30 yields confidence 0.30/1.30 = 0.23, below the 0.30 floor.
    context = _context(AssetId.NEM_NYSE)
    repo = _FakeRepo(claimed=[context], events={context.context_id: [_ctx_event()]})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.30)],
        corr_edges=[_correlation(AssetId.NEM_NYSE, AssetId.XOM_NYSE, Direction.DOWN)],
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    assets = [msg.asset_id for msg, _ in repo.stored]
    assert assets == [AssetId.NEM_NYSE], "a weak decision propagated anyway"


async def test_strong_source_still_propagates() -> None:
    # The gate is a floor on evidence, not a switch: a strong source must still reach downstream.
    context = _context(AssetId.NEM_NYSE)
    repo = _FakeRepo(claimed=[context], events={context.context_id: [_ctx_event()]})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.90)],
        corr_edges=[_correlation(AssetId.NEM_NYSE, AssetId.XOM_NYSE, Direction.DOWN)],
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    assets = [msg.asset_id for msg, _ in repo.stored]
    assert AssetId.XOM_NYSE in assets


async def test_two_corroborating_edges_clear_the_gate() -> None:
    # Two agreeing edges at w=0.5 give confidence 0.50, comfortably over the floor. This is the case
    # the threshold was chosen around, so it is pinned.
    context = _context(AssetId.NEM_NYSE)
    repo = _FakeRepo(
        claimed=[context],
        events={
            context.context_id: [
                _ctx_event(EventType.MILITARY_CONFLICT),
                _ctx_event(EventType.INFLATION_CHANGE),
            ]
        },
    )
    graph = _FakeGraph(
        edges=[
            _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.50),
            _edge(EventType.INFLATION_CHANGE, Direction.UP, 0.50),
        ],
        corr_edges=[_correlation(AssetId.NEM_NYSE, AssetId.XOM_NYSE, Direction.DOWN)],
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    assert AssetId.XOM_NYSE in [msg.asset_id for msg, _ in repo.stored]


# --- PRD-61: a propagated stance never overturns a standing one ----------------------------------


async def test_propagation_does_not_contradict_an_active_stance() -> None:
    # Every asset already holds UP; propagation would push NEM DOWN. The standing stance wins,
    # because it has a causal factor behind it and the propagated one does not. Only XOM's context
    # is claimed, so NEM can be reached by propagation alone.
    context_xom = _context(AssetId.XOM_NYSE)
    repo = _FakeRepo(
        claimed=[context_xom],
        events={context_xom.context_id: [_ctx_event()]},
        active=ActivePrediction(
            prediction_id=uuid.uuid4(), direction=Direction.UP, magnitude=Magnitude.MEDIUM
        ),
    )
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.90, asset=AssetId.XOM_NYSE)],
        corr_edges=[_correlation(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN)],
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    nem = [msg for msg, _ in repo.stored if msg.asset_id is AssetId.NEM_NYSE]
    assert nem == [], "a propagated prediction overturned a standing opposing stance"


async def test_a_directly_decided_asset_is_not_reached_by_propagation() -> None:
    """The structural contradiction from 2026-08-12, in one batch.

    One event reaches both the gold and the oil proxy. Each opens its own context and each would
    propagate a contradiction onto the other. Deciding all direct stances before any propagation is
    what lets propagation stand down.
    """
    gold_ctx = _context(AssetId.NEM_NYSE)
    oil_ctx = _context(AssetId.XOM_NYSE)
    shared_event = _ctx_event()
    repo = _FakeRepo(
        claimed=[gold_ctx, oil_ctx],
        events={
            gold_ctx.context_id: [shared_event],
            oil_ctx.context_id: [shared_event],
        },
    )
    graph = _FakeGraph(
        edges=[
            _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.90),
            _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.90, asset=AssetId.XOM_NYSE),
        ],
        corr_edges=[
            _correlation(AssetId.NEM_NYSE, AssetId.XOM_NYSE, Direction.DOWN),
            _correlation(AssetId.XOM_NYSE, AssetId.NEM_NYSE, Direction.DOWN),
        ],
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    directions: dict[AssetId, set[Direction]] = {}
    for msg, _ in repo.stored:
        directions.setdefault(msg.asset_id, set()).add(msg.direction)
    for asset, dirs in directions.items():
        assert len(dirs) == 1, f"{asset.value} took opposing stances from one event: {dirs}"
    assert directions[AssetId.NEM_NYSE] == {Direction.UP}
    assert directions[AssetId.XOM_NYSE] == {Direction.UP}


# --- PRD-62: provenance is the evidence, not the window ------------------------------------------


async def test_event_ids_name_only_the_contributing_events() -> None:
    context = _context(AssetId.NEM_NYSE)
    driver = _ctx_event(EventType.MILITARY_CONFLICT)
    # Two unrelated events that merely landed in the same 15-minute window.
    noise_one = _ctx_event(EventType.SPORT)
    noise_two = _ctx_event(EventType.ENTERTAINMENT)
    repo = _FakeRepo(
        claimed=[context],
        events={context.context_id: [driver, noise_one, noise_two]},
    )
    graph = _FakeGraph(edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.50)])
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    message, _ = repo.stored[0]
    assert message.event_ids == [driver.event_id]
    assert noise_one.event_id not in message.event_ids
    assert noise_two.event_id not in message.event_ids


async def test_event_ids_name_every_contributing_factor() -> None:
    context = _context(AssetId.NEM_NYSE)
    conflict = _ctx_event(EventType.MILITARY_CONFLICT)
    inflation = _ctx_event(EventType.INFLATION_CHANGE)
    noise = _ctx_event(EventType.SPORT)
    repo = _FakeRepo(
        claimed=[context],
        events={context.context_id: [conflict, inflation, noise]},
    )
    graph = _FakeGraph(
        edges=[
            _edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.50),
            _edge(EventType.INFLATION_CHANGE, Direction.UP, 0.45),
        ]
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    message, _ = repo.stored[0]
    assert set(message.event_ids) == {conflict.event_id, inflation.event_id}


async def test_a_propagated_prediction_still_carries_evidence() -> None:
    """A propagated decision has no causal factor, so the filter would empty its evidence.

    It falls back to the source context's events instead. A prediction with imprecise evidence is
    recoverable; one with none cannot be explained or audited at all.
    """
    context = _context(AssetId.NEM_NYSE)
    driver = _ctx_event(EventType.MILITARY_CONFLICT)
    repo = _FakeRepo(claimed=[context], events={context.context_id: [driver]})
    graph = _FakeGraph(
        edges=[_edge(EventType.MILITARY_CONFLICT, Direction.UP, 0.90)],
        corr_edges=[_correlation(AssetId.NEM_NYSE, AssetId.XOM_NYSE, Direction.DOWN)],
    )
    await _pipeline(repo, graph).close_ready_contexts(_NOW)

    propagated = [msg for msg, _ in repo.stored if msg.propagation_depth > 0]
    assert propagated, "nothing propagated, so the fallback was not exercised"
    for message in propagated:
        assert message.event_ids, "a propagated prediction was stored with no evidence"
        assert message.event_ids == [driver.event_id]
