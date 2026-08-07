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
    def __init__(self) -> None:
        self.calls: list[_Upsert] = []

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
    ) -> None:
        self.calls.append(
            _Upsert(
                factor_id, condition, asset_id, direction, weight, confidence, alpha, beta
            )
        )


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
