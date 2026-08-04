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
        assets=[AssetId.BRENT_OIL],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[ConditionCode.TRANSPORT_AFFECTED],
        first_seen_at=_DECISION_AT,
    )
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("BRENT_OIL", _BASELINE): Decimal("100"),
            ("BRENT_OIL", _SETTLEMENT): Decimal("105"),
        },
    )
    samples = await build_samples(_FakePool(conn), lookback_days=365)

    # One unconditional (None) + one TRANSPORT_AFFECTED sample, both +5% return.
    assert len(samples) == 2
    assert {s.condition for s in samples} == {None, ConditionCode.TRANSPORT_AFFECTED}
    for sample in samples:
        assert sample.factor is EventType.MILITARY_CONFLICT
        assert sample.asset is AssetId.BRENT_OIL
        assert sample.actual_return == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_skips_event_with_missing_price() -> None:
    payload = _event_payload(
        event_type=EventType.SANCTIONS,
        assets=[AssetId.GOLD],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={("GOLD", _BASELINE): Decimal("2000")},  # settlement close missing
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
        assets=[AssetId.BRENT_OIL],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    vol_closes = [Decimal(str(100 + (i % 2))) for i in range(30)]  # alternates 100/101 -> ~1% vol
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("BRENT_OIL", _BASELINE): Decimal("100"),
            ("BRENT_OIL", _SETTLEMENT): Decimal("110"),  # +10% move
        },
        volatility_closes={"BRENT_OIL": vol_closes},
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
        assets=[AssetId.BRENT_OIL],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    vol_closes = [Decimal(str(100 + (i % 2))) for i in range(30)]
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("BRENT_OIL", _BASELINE): Decimal("100"),
            ("BRENT_OIL", _SETTLEMENT): Decimal("100.5"),  # tiny move
        },
        volatility_closes={"BRENT_OIL": vol_closes},
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
        assets=[AssetId.BRENT_OIL],
        polarity=EventPolarity.OCCURRENCE,
        context_tags=[],
        first_seen_at=_DECISION_AT,
    )
    conn = _FakeConnection(
        rows=[{"event_id": uuid.uuid4(), "payload": payload}],
        closes={
            ("BRENT_OIL", _BASELINE): Decimal("100"),
            ("BRENT_OIL", _SETTLEMENT): Decimal("120"),
        },
        volatility_closes={},  # no history
    )
    samples = await build_samples(
        _FakePool(conn), lookback_days=365, volatility_lookback_days=30, abnormal_threshold=2.0
    )
    assert len(samples) == 1
    assert samples[0].is_abnormal is False
    assert samples[0].asset_volatility == 0.0
