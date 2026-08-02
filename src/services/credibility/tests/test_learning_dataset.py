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

from credibility.learning.dataset import build_samples


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
    def __init__(self, rows: list[dict[str, Any]], closes: dict[tuple[str, date], Decimal]) -> None:
        self._rows = rows
        self._closes = closes
        self.fetchval_calls = 0

    async def fetch(self, _query: str, *_args: Any) -> list[dict[str, Any]]:
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
