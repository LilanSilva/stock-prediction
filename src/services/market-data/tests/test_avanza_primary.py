from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from shared.reference import resolve
from shared.schemas.messages import AssetId, CloseObservation, PriceKind, PriceSampleObserved

from market_data.avanza import (
    AvanzaFirstPrices,
    AvanzaPending,
    AvanzaUnavailable,
    minute_values,
    provenance,
    summarize,
    validate_samples,
)
from market_data.snapshots.calendar import Session

ASSET = AssetId.TSLA_NASDAQ
SERIES = resolve(ASSET)
OPEN = datetime(2026, 10, 6, 13, 30, tzinfo=UTC)
WINDOW = Session(
    session=OPEN.date(),
    opens_at=OPEN,
    closes_at=OPEN + timedelta(minutes=30),
    next_open_at=OPEN + timedelta(days=1),
)
NOW = WINDOW.closes_at + timedelta(minutes=3)


def sample(minute: int, price: str = "20.32", **updates: object) -> PriceSampleObserved:
    values = dict(
        sample_id=uuid4(),
        correlation_id=uuid4(),
        occurred_at=OPEN,
        asset_id=ASSET,
        mapping_version="test-v1",
        registry_version=SERIES.registry_version,
        session=OPEN.date(),
        opens_at=OPEN,
        closes_at=WINDOW.closes_at,
        scheduled_at=OPEN + timedelta(minutes=minute),
        observed_at=OPEN + timedelta(minutes=minute, seconds=5),
        price=price,
        currency=SERIES.currency,
        quote_unit=SERIES.currency,
        interval_seconds=600,
        kind="REGULAR",
        market_state="REGULAR_OPEN",
        quality="FRESHNESS_UNKNOWN",
        quote_delay_seconds=900,
    )
    values.update(updates)
    return PriceSampleObserved(**values)


def validate(prices: list[PriceSampleObserved]) -> list[PriceSampleObserved]:
    return validate_samples(prices, ASSET, WINDOW, SERIES.currency, SERIES.registry_version, NOW)


def test_sampled_ohlc_and_provisional_session() -> None:
    prices = validate([sample(0, "20.32"), sample(10, "25.21"), sample(20, "19.25")])
    result = summarize(ASSET, WINDOW, prices, NOW)
    assert (result.open, result.high, result.low, result.close) == tuple(
        map(Decimal, ("20.32", "25.21", "19.25", "19.25"))
    )
    assert result.complete and not result.provisional
    assert result.last_sample.provider_quote_at is None
    assert result.last_sample.quote_delay_seconds == 900
    live = summarize(ASSET, WINDOW, prices[:2], OPEN + timedelta(minutes=15))
    assert live.provisional and not live.complete and live.missing_slots == []


def test_carry_forward_uses_original_observation_and_stops_at_missing_slot() -> None:
    first, second = sample(0), sample(10, "25.21")
    bars = minute_values([first, second], NOW)
    assert len(bars) == 20
    assert [b.close for b in bars[:10]] == [Decimal("20.32")] * 10
    assert [b.close for b in bars[10:]] == [Decimal("25.21")] * 10
    assert not bars[0].carried_forward and bars[9].carried_forward
    assert bars[9].sample.observed_at == first.observed_at
    assert bars[-1].start == OPEN + timedelta(minutes=19)
    assert all(b.start + timedelta(minutes=1) <= NOW for b in bars)


def test_no_future_or_pre_observation_minutes() -> None:
    late = sample(0, observed_at=OPEN + timedelta(seconds=65))
    bars = minute_values([late], OPEN + timedelta(minutes=3, seconds=30))
    assert [b.start for b in bars] == [OPEN + timedelta(minutes=m) for m in (1, 2)]
    assert minute_values([late], OPEN) == []


def test_missing_expected_slot_is_explicit() -> None:
    result = summarize(ASSET, WINDOW, validate([sample(0), sample(20)]), NOW)
    assert result.missing_slots == [OPEN + timedelta(minutes=10)]
    assert not result.complete


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "EUR"},
        {"quality": "STALE"},
        {"market_state": "UNKNOWN"},
        {"observed_at": OPEN + timedelta(seconds=121)},
        {"registry_version": "wrong"},
        {"scheduled_at": OPEN + timedelta(seconds=1)},
    ],
)
def test_invalid_evidence_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(AvanzaUnavailable):
        validate([sample(0, **changes)])


def test_close_checks_and_future_reads_are_not_regular_evidence() -> None:
    closing = sample(30, kind="CLOSE_CHECK", market_state="REGULAR_CLOSED")
    assert validate([closing]) == []
    with pytest.raises(AvanzaUnavailable, match="MIXED_MAPPING"):
        validate([sample(0), sample(10, mapping_version="new")])


def close(day: date, avanza: bool = False) -> CloseObservation:
    return CloseObservation(
        session=day,
        close="20.32",
        fetched_at=NOW,
        source="avanza" if avanza else SERIES.provider,
        provider_symbol=SERIES.provider_symbol,
        price_kind=PriceKind.AVANZA_SAMPLED_CLOSE if avanza else SERIES.price_kind,
        is_adjusted=False,
        registry_version=SERIES.registry_version,
        sample=provenance(sample(20)) if avanza else None,
    )


def prices(reader_close: AsyncMock) -> tuple[AvanzaFirstPrices, SimpleNamespace]:
    reader = SimpleNamespace(window=lambda *_: WINDOW, close=reader_close)
    finance = SimpleNamespace(get_close=AsyncMock(side_effect=lambda _, day: close(day)))
    return AvanzaFirstPrices(reader, finance), finance


async def test_open_session_does_not_trigger_finance_fallback() -> None:
    service, finance = prices(AsyncMock(side_effect=RuntimeError("not queried")))
    with pytest.raises(AvanzaPending):
        await service.get_pair(ASSET, OPEN.date(), OPEN.date(), OPEN + timedelta(minutes=15))
    finance.get_close.assert_not_awaited()


async def test_valid_avanza_pair_makes_zero_finance_calls() -> None:
    service, finance = prices(AsyncMock(side_effect=lambda _, day, now: close(day, True)))
    first, last = await service.get_pair(ASSET, OPEN.date(), OPEN.date(), NOW)
    assert first.source == last.source == "avanza"
    finance.get_close.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [AvanzaUnavailable("MISSING_AVANZA_SLOT"), RuntimeError("secret")]
)
async def test_partial_avanza_pair_falls_back_as_a_whole(error: Exception) -> None:
    service, finance = prices(AsyncMock(side_effect=[close(OPEN.date(), True), error]))
    first, last = await service.get_pair(ASSET, OPEN.date(), OPEN.date(), NOW)
    assert first.source == last.source == SERIES.provider
    assert first.sample is None and last.sample is None
    assert finance.get_close.await_count == 2
    assert "secret" not in first.fallback_reason


async def test_both_sources_failed_is_unavailable() -> None:
    service, finance = prices(AsyncMock(side_effect=AvanzaUnavailable("NO_HISTORY")))
    finance.get_close.side_effect = RuntimeError("finance unavailable")
    with pytest.raises(RuntimeError, match="finance unavailable"):
        await service.get_pair(ASSET, OPEN.date(), OPEN.date(), NOW)


async def test_cancelled_read_does_not_trigger_fallback() -> None:
    import asyncio

    service, finance = prices(AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await service.get_pair(ASSET, OPEN.date(), OPEN.date(), NOW)
    finance.get_close.assert_not_awaited()


async def test_recent_prefers_avanza_and_fills_only_missing_sessions() -> None:
    older = OPEN.date() - timedelta(days=1)
    finance_value = close(older)
    reader = SimpleNamespace(
        recent_sessions=lambda *_: [OPEN.date(), older],
        close=AsyncMock(side_effect=[close(OPEN.date(), True), AvanzaUnavailable("NO_HISTORY")]),
        pool=SimpleNamespace(fetch=AsyncMock(return_value=[finance_value.model_dump()])),
    )
    finance = SimpleNamespace(fetch_observations=AsyncMock())
    values = await AvanzaFirstPrices(reader, finance).recent(ASSET, 2, NOW)
    assert [v.source for v in values] == ["avanza", SERIES.provider]
    assert values[1].fallback_reason == "NO_HISTORY"
    finance.fetch_observations.assert_not_awaited()
