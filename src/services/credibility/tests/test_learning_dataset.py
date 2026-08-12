"""Light tests for the read-only dataset builder using fake asyncpg objects.

Verifies the return computation, per-asset / per-condition sample expansion, and that events with
missing price data are skipped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from shared.calendar import NEW_YORK, resolve_baseline_settlement
from shared.schemas.messages import (
    AssetId,
    ConditionCode,
    EventDetected,
    EventPolarity,
    EventType,
    ExtractionMethod,
)

from credibility.learning.dataset import _calculate_volatility, build_samples


def _event_payload(
    *,
    event_type: EventType,
    assets: list[AssetId],
    polarity: EventPolarity,
    context_tags: list[ConditionCode],
    first_seen_at: datetime,
) -> str:
    event = EventDetected(
        correlation_id=uuid.uuid4(),
        occurred_at=first_seen_at,
        event_id=uuid.uuid4(),
        cluster_id=uuid.uuid4(),
        canonical_summary="summary",
        event_type=event_type,
        affected_asset_ids=assets,
        polarity=polarity,
        context_tags=context_tags,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        extraction_method=ExtractionMethod.LOCAL,
    )
    return event.model_dump_json()


class _FakeConnection:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        closes: dict[tuple[str, date], Decimal],
        volatility_closes: dict[str, list[Decimal]] | None = None,
    ) -> None:
        self._rows = rows
        self._closes = closes
        self._volatility_closes: dict[str, list[Decimal]] = volatility_closes or {}
        self.fetchval_calls = 0

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        # Events query returns the event rows; volatility query returns close rows per asset.
        if "close_observations" in query and len(args) == 2:
            asset_id = args[0]
            return [{"close": c} for c in self._volatility_closes.get(asset_id, [])]
        return self._rows

    async def fetchval(self, _query: str, asset_id: str, session: date) -> Decimal | None:
        self.fetchval_calls += 1
        return self._closes.get((asset_id, session))


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


# Derive the exact sessions the resolver will use, rather than hard-coding them.
_DECISION_AT = datetime(2026, 7, 29, 21, 0, tzinfo=UTC)
_BASELINE, _SETTLEMENT = resolve_baseline_settlement(_DECISION_AT, NEW_YORK)


@pytest.mark.asyncio
async def test_builds_one_sample_per_asset_and_condition() -> None:
    payload = _event_payload(
        event_type=EventType.MILITARY_CONFLICT,
        assets=[AssetId.XOM_NYSE],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        first_seen_at=_DECISION_AT,
    )
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("XOM_NYSE", _BASELINE): Decimal("100"),
            ("XOM_NYSE", _SETTLEMENT): Decimal("105"),
        },
    )
    samples = await build_samples(_FakePool(conn), lookback_days=365)

    # One unconditional (None) + one TRANSPORT_AFFECTED sample, both +5% return.
    assert len(samples) == 2
    assert {s.condition for s in samples} == {None, ConditionCode.TRANSPORT_AFFECTED}
    for sample in samples:
        assert sample.factor is EventType.MILITARY_CONFLICT
        assert sample.asset is AssetId.XOM_NYSE
        assert sample.actual_return == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_skips_event_with_missing_price() -> None:
    payload = _event_payload(
        event_type=EventType.SANCTIONS,
        assets=[AssetId.NEM_NYSE],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={("NEM_NYSE", _BASELINE): Decimal("2000")},  # settlement close missing
    )
    samples = await build_samples(_FakePool(conn), lookback_days=365)
    assert samples == []


# --- _calculate_volatility unit tests ---


def test_calculate_volatility_returns_zero_for_single_close() -> None:
    from credibility.learning.dataset import _calculate_volatility
    assert _calculate_volatility([100.0]) == 0.0


def test_calculate_volatility_returns_zero_for_empty() -> None:
    assert _calculate_volatility([]) == 0.0


def test_calculate_volatility_known_sequence() -> None:
    # Two closes: 100 -> 110, one daily return of +10%. Std dev of [0.1] = 0.0.
    assert _calculate_volatility([100.0, 110.0]) == pytest.approx(0.0)


def test_calculate_volatility_nonzero_for_varying_returns() -> None:
    # 100 -> 110 -> 99: returns [+10%, -10%]. Should produce non-zero std dev.
    vol = _calculate_volatility([100.0, 110.0, 99.0])
    assert vol > 0.0


# --- abnormal tagging tests ---


@pytest.mark.asyncio
async def test_sample_tagged_abnormal_when_return_exceeds_threshold() -> None:
    # Daily closes showing ~1% volatility; event moves +10% -> abnormal at 2x threshold.
    payload = _event_payload(
        event_type=EventType.MILITARY_CONFLICT,
        assets=[AssetId.XOM_NYSE],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    vol_closes = [Decimal(str(100 + (i % 2))) for i in range(30)]  # alternates 100/101 -> ~1% vol
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("XOM_NYSE", _BASELINE): Decimal("100"),
            ("XOM_NYSE", _SETTLEMENT): Decimal("110"),  # +10% move
        },
        volatility_closes={"XOM_NYSE": vol_closes},
    )
    samples = await build_samples(
        _FakePool(conn), lookback_days=365, volatility_lookback_days=30, abnormal_threshold=2.0
    )
    assert len(samples) == 1  # no context_tags so only unconditional sample
    assert samples[0].is_abnormal is True
    assert samples[0].asset_volatility > 0.0


@pytest.mark.asyncio
async def test_sample_tagged_normal_when_return_within_threshold() -> None:
    payload = _event_payload(
        event_type=EventType.MILITARY_CONFLICT,
        assets=[AssetId.XOM_NYSE],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    vol_closes = [Decimal(str(100 + (i % 2))) for i in range(30)]
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("XOM_NYSE", _BASELINE): Decimal("100"),
            ("XOM_NYSE", _SETTLEMENT): Decimal("100.5"),  # tiny move
        },
        volatility_closes={"XOM_NYSE": vol_closes},
    )
    samples = await build_samples(
        _FakePool(conn), lookback_days=365, volatility_lookback_days=30, abnormal_threshold=2.0
    )
    assert len(samples) == 1
    assert samples[0].is_abnormal is False


@pytest.mark.asyncio
async def test_sample_tagged_normal_when_no_volatility_history() -> None:
    # No volatility closes available -> volatility=0.0 -> is_abnormal=False regardless of move.
    payload = _event_payload(
        event_type=EventType.MILITARY_CONFLICT,
        assets=[AssetId.XOM_NYSE],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("XOM_NYSE", _BASELINE): Decimal("100"),
            ("XOM_NYSE", _SETTLEMENT): Decimal("120"),
        },
        volatility_closes={},  # no history
    )
    samples = await build_samples(
        _FakePool(conn), lookback_days=365, volatility_lookback_days=30, abnormal_threshold=2.0
    )
    assert len(samples) == 1
    assert samples[0].is_abnormal is False
    assert samples[0].asset_volatility == 0.0


# --- build_correlation_samples tests ---

from shared.graph.models import CorrelationEdge  # noqa: E402
from shared.schemas.messages import Direction  # noqa: E402

from credibility.learning.dataset import build_correlation_samples  # noqa: E402


class _FakeCorrConnection:
    """Fake connection that returns direct-prediction rows from the correlation query."""

    def __init__(
        self,
        prediction_rows: list[dict[str, Any]],
        closes: dict[tuple[str, date], Decimal],
        volatility_closes: dict[str, list[Decimal]] | None = None,
    ) -> None:
        self._prediction_rows = prediction_rows
        self._closes = closes
        self._volatility_closes: dict[str, list[Decimal]] = volatility_closes or {}

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "close_observations" in query and len(args) == 2:
            asset_id = args[0]
            return [{"close": c} for c in self._volatility_closes.get(asset_id, [])]
        # The direct scored-predictions query returns prediction rows.
        return self._prediction_rows

    async def fetchval(self, _query: str, asset_id: str, session: date) -> Decimal | None:
        return self._closes.get((asset_id, session))


class _FakeCorrAcquire:
    def __init__(self, conn: _FakeCorrConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeCorrConnection:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakeCorrPool:
    def __init__(self, conn: _FakeCorrConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeCorrAcquire:
        return _FakeCorrAcquire(self._conn)


class _FakeGraphCorr:
    """Graph stub that returns pre-configured CORRELATES_WITH edges."""

    def __init__(
        self,
        edges: dict[tuple[str, str], list[CorrelationEdge]] | None = None,
    ) -> None:
        # keyed by (source_asset_id.value, condition.value)
        self._edges: dict[tuple[str, str], list[CorrelationEdge]] = edges or {}

    async def get_correlation_edges(
        self, source_asset_id: AssetId, condition: ConditionCode
    ) -> list[CorrelationEdge]:
        return self._edges.get((source_asset_id.value, condition.value), [])


def _corr_edge_obj(
    source: AssetId,
    target: AssetId,
    condition: ConditionCode = ConditionCode.UPSTREAM_UP,
) -> CorrelationEdge:
    return CorrelationEdge(
        source_asset_id=source,
        target_asset_id=target,
        condition=condition,
        direction=Direction.DOWN,
        weight=0.45,
        confidence=0.8,
        alpha=2.0,
        beta=1.0,
    )


@pytest.mark.asyncio
async def test_build_correlation_samples_empty_when_no_prediction_rows() -> None:
    conn = _FakeCorrConnection(prediction_rows=[], closes={})
    graph = _FakeGraphCorr()
    samples = await build_correlation_samples(
        _FakeCorrPool(conn), graph, lookback_days=365
    )
    assert samples == []


@pytest.mark.asyncio
async def test_build_correlation_samples_skips_when_no_corr_edges() -> None:
    prediction_row = {
        "source_asset_id": "XOM_NYSE",
        "predicted_direction": "UP",
        "settlement_session": _SETTLEMENT,
        "baseline_session": _BASELINE,
    }
    conn = _FakeCorrConnection(prediction_rows=[prediction_row], closes={})
    graph = _FakeGraphCorr()  # no edges registered -> empty list returned
    samples = await build_correlation_samples(
        _FakeCorrPool(conn), graph, lookback_days=365
    )
    assert samples == []


@pytest.mark.asyncio
async def test_build_correlation_samples_produces_sample() -> None:
    prediction_row = {
        "source_asset_id": "XOM_NYSE",
        "predicted_direction": "UP",
        "settlement_session": _SETTLEMENT,
        "baseline_session": _BASELINE,
    }
    conn = _FakeCorrConnection(
        prediction_rows=[prediction_row],
        closes={
            ("NEM_NYSE", _BASELINE): Decimal("2000"),
            ("NEM_NYSE", _SETTLEMENT): Decimal("1960"),
        },
    )
    edge = _corr_edge_obj(AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP)
    graph = _FakeGraphCorr(
        edges={("XOM_NYSE", "UPSTREAM_UP"): [edge]}
    )
    samples = await build_correlation_samples(
        _FakeCorrPool(conn), graph, lookback_days=365
    )
    assert len(samples) == 1
    s = samples[0]
    assert s.source_asset is AssetId.XOM_NYSE
    assert s.target_asset is AssetId.NEM_NYSE
    assert s.condition is ConditionCode.UPSTREAM_UP
    assert s.actual_return == pytest.approx(-0.02)


@pytest.mark.asyncio
async def test_build_correlation_samples_skips_target_with_missing_price() -> None:
    prediction_row = {
        "source_asset_id": "XOM_NYSE",
        "predicted_direction": "UP",
        "settlement_session": _SETTLEMENT,
        "baseline_session": _BASELINE,
    }
    # Baseline provided but settlement missing — should be skipped.
    conn = _FakeCorrConnection(
        prediction_rows=[prediction_row],
        closes={("NEM_NYSE", _BASELINE): Decimal("2000")},  # no settlement
    )
    edge = _corr_edge_obj(AssetId.XOM_NYSE, AssetId.NEM_NYSE, ConditionCode.UPSTREAM_UP)
    graph = _FakeGraphCorr(edges={("XOM_NYSE", "UPSTREAM_UP"): [edge]})
    samples = await build_correlation_samples(
        _FakeCorrPool(conn), graph, lookback_days=365
    )
    assert samples == []
