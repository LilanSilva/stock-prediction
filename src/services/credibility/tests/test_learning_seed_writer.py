"""Tests for the seed writer: only conditioned, non-NEUTRAL estimates are upserted."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from shared.schemas.messages import AssetId, ConditionCode, Direction, EventType

from credibility.learning.models import EdgeEstimate
from credibility.learning.seed_writer import write_estimates


@dataclass
class _Upsert:
    factor_id: EventType
    condition: ConditionCode
    asset_id: AssetId
    direction: Direction
    weight: float
    confidence: float
    alpha: float
    beta: float


class _FakeGraph:
    """Records upsert calls. ``creates`` mimics the add-only return: True when the edge was new.

    The real client reports this from Neo4j's `relationships_created` counter, so an estimate for an
    edge that already exists comes back False and must not be counted as written.
    """

    def __init__(self, *, creates: bool = True) -> None:
        self.calls: list[_Upsert] = []
        self._creates = creates

    async def upsert_conditioned_edge(
        self,
        factor_id: EventType,
        condition: ConditionCode,
        asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> bool:
        self.calls.append(
            _Upsert(
                factor_id, condition, asset_id, direction, weight, confidence, alpha, beta
            )
        )
        return self._creates


def _estimate(
    *,
    condition: ConditionCode | None,
    direction: Direction,
) -> EdgeEstimate:
    return EdgeEstimate(
        factor=EventType.MILITARY_CONFLICT,
        condition=condition,
        asset=AssetId.XOM_NYSE,
        direction=direction,
        weight=0.6,
        confidence=0.9,
        alpha=9.0,
        beta=2.0,
        sample_count=10,
    )


@pytest.mark.asyncio
async def test_writes_conditioned_directional_edges() -> None:
    graph = _FakeGraph()
    estimates = [
        _estimate(condition=ConditionCode.TRANSPORT_AFFECTED, direction=Direction.UP),
        _estimate(condition=ConditionCode.SAFE_HAVEN_ONLY, direction=Direction.DOWN),
    ]
    written = await write_estimates(graph, estimates)
    assert written == 2
    assert {c.condition for c in graph.calls} == {
        ConditionCode.TRANSPORT_AFFECTED,
        ConditionCode.SAFE_HAVEN_ONLY,
    }
    assert graph.calls[0].alpha == 9.0
    assert graph.calls[0].beta == 2.0


@pytest.mark.asyncio
async def test_skips_neutral_and_unconditional_estimates() -> None:
    graph = _FakeGraph()
    estimates = [
        _estimate(condition=ConditionCode.TRANSPORT_AFFECTED, direction=Direction.NEUTRAL),
        _estimate(condition=None, direction=Direction.UP),
        _estimate(condition=ConditionCode.TRANSPORT_AFFECTED, direction=Direction.UP),
    ]
    written = await write_estimates(graph, estimates)
    assert written == 1
    assert len(graph.calls) == 1
    assert graph.calls[0].condition is ConditionCode.TRANSPORT_AFFECTED


# --- write_correlation_estimates tests ---

from dataclasses import dataclass as _dc  # noqa: E402

import pytest as _pytest  # noqa: E402

from credibility.learning.models import CorrelationEdgeEstimate  # noqa: E402
from credibility.learning.seed_writer import write_correlation_estimates  # noqa: E402


@_dc
class _CorrUpsert:
    source_asset_id: AssetId
    condition: ConditionCode
    target_asset_id: AssetId
    direction: Direction
    weight: float
    confidence: float
    alpha: float
    beta: float


class _FakeCorrGraph:
    def __init__(self, *, creates: bool = True) -> None:
        self.calls: list[_CorrUpsert] = []
        self._creates = creates

    async def upsert_correlation_edge(
        self,
        source_asset_id: AssetId,
        condition: ConditionCode,
        target_asset_id: AssetId,
        *,
        direction: Direction,
        weight: float,
        confidence: float,
        alpha: float,
        beta: float,
    ) -> bool:
        self.calls.append(
            _CorrUpsert(
                source_asset_id,
                condition,
                target_asset_id,
                direction,
                weight,
                confidence,
                alpha,
                beta,
            )
        )
        return self._creates


def _corr_estimate(*, direction: Direction) -> CorrelationEdgeEstimate:
    return CorrelationEdgeEstimate(
        source_asset=AssetId.XOM_NYSE,
        condition=ConditionCode.UPSTREAM_UP,
        target_asset=AssetId.NEM_NYSE,
        direction=direction,
        weight=0.5,
        confidence=0.8,
        alpha=3.0,
        beta=2.0,
        sample_count=5,
    )


@_pytest.mark.asyncio
async def test_write_correlation_estimates_skips_neutral() -> None:
    graph = _FakeCorrGraph()
    written = await write_correlation_estimates(
        graph, [_corr_estimate(direction=Direction.NEUTRAL)]
    )
    assert written == 0
    assert graph.calls == []


@_pytest.mark.asyncio
async def test_write_correlation_estimates_calls_upsert_for_directional() -> None:
    graph = _FakeCorrGraph()
    written = await write_correlation_estimates(
        graph,
        [
            _corr_estimate(direction=Direction.DOWN),
            _corr_estimate(direction=Direction.UP),
        ],
    )
    assert written == 2
    assert len(graph.calls) == 2


@_pytest.mark.asyncio
async def test_write_correlation_estimates_empty_input_returns_zero() -> None:
    graph = _FakeCorrGraph()
    written = await write_correlation_estimates(graph, [])
    assert written == 0


# --- add-only reporting: an estimate for an existing edge is a no-op, not a write ---


@_pytest.mark.asyncio
async def test_existing_edges_are_attempted_but_not_counted_as_written() -> None:
    # The learner is add-only, so an estimate for an edge that already exists changes nothing. The
    # count must reflect edges CREATED, otherwise the log claims graph changes that never happened.
    graph = _FakeGraph(creates=False)
    estimates = [
        _estimate(condition=ConditionCode.TRANSPORT_AFFECTED, direction=Direction.UP),
        _estimate(condition=ConditionCode.SAFE_HAVEN_ONLY, direction=Direction.DOWN),
    ]
    written = await write_estimates(graph, estimates)
    assert written == 0, "existing edges must not be reported as written"
    assert len(graph.calls) == 2, "both were still attempted"


@_pytest.mark.asyncio
async def test_existing_correlation_edges_are_not_counted_as_written() -> None:
    graph = _FakeCorrGraph(creates=False)
    written = await write_correlation_estimates(
        graph, [_corr_estimate(direction=Direction.DOWN)]
    )
    assert written == 0
    assert len(graph.calls) == 1
