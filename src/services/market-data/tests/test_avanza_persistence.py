"""Real database checks use only the disposable snapshot_test database."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from shared.schemas.messages import IntradayObserved, IntradayRequested, PriceRequested

from market_data.avanza import AvanzaReader, AvanzaUnavailable, minute_values
from market_data.config import MarketDataSettings
from market_data.db import SCHEMA_DDL
from market_data.intraday import DDL, IntradayCollector
from market_data.storage import PriceRequestRepository, build_price_observed
from tests.test_avanza_primary import ASSET, NOW, OPEN, SERIES, WINDOW, sample
from tests.test_snapshot_storage import pool as pool

pytestmark = pytest.mark.integration


async def seed_samples(pool: Any) -> None:
    await pool.execute("INSERT INTO market_data.snapshot_mappings VALUES('test-v1','hash','{}')")
    for minute in (0, 10, 20):
        value = sample(minute)
        await pool.execute(
            "INSERT INTO market_data.snapshot_jobs "
            "(job_id,asset_id,session,version,registry_version,listing,session_window,scheduled_at,"
            "kind,state,next_attempt_at) VALUES($1,$2,$3,'test-v1',$4,'{}','{}',$5,"
            "'REGULAR','SUCCEEDED',$5)",
            value.sample_id,
            str(ASSET),
            OPEN.date(),
            SERIES.registry_version,
            value.scheduled_at,
        )
        await pool.execute(
            "INSERT INTO market_data.price_samples VALUES"
            "($1,$2,$3,'test-v1',$4,'test-id','TEST',$5,$6,$6,$7,$8,NULL,'REGULAR',"
            "'FRESHNESS_UNKNOWN',$9)",
            value.sample_id,
            str(ASSET),
            OPEN.date(),
            SERIES.registry_version,
            value.price,
            SERIES.currency,
            value.scheduled_at,
            value.observed_at,
            value.model_dump_json(),
        )


async def test_real_read_persist_and_replay_leave_finance_history_untouched(
    pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await pool.execute(SCHEMA_DDL)
    await pool.execute(SCHEMA_DDL)
    await seed_samples(pool)
    reader = AvanzaReader(pool, Path("unused"))
    monkeypatch.setattr(reader, "window", lambda *_: WINDOW)
    monkeypatch.setattr(
        reader,
        "listing",
        lambda *_: SimpleNamespace(instrument_id="test-id", expected_exchange="TEST"),
    )
    observation = await reader.close(ASSET, OPEN.date(), NOW)
    assert observation.sample.observed_at == OPEN + timedelta(minutes=20, seconds=5)
    assert len(await reader.minutes(ASSET, OPEN.date(), NOW)) == 30
    await pool.execute(
        "INSERT INTO market_data.close_observations "
        "(asset_id,session,close,fetched_at,source,provider_symbol,price_kind,is_adjusted,"
        "registry_version,content_hash) VALUES($1,$2,99.50,$3,$4,$5,'PROVIDER_DAILY_CLOSE',"
        "false,$6,'original-finance') ON CONFLICT DO NOTHING",
        str(ASSET), OPEN.date(), NOW, SERIES.provider, SERIES.provider_symbol,
        SERIES.registry_version,
    )
    before = await pool.fetchval("SELECT count(*) FROM market_data.close_observations")
    request = PriceRequested(
        request_id=uuid4(),
        prediction_id=uuid4(),
        correlation_id=uuid4(),
        occurred_at=NOW,
        asset_id=ASSET,
        baseline_session=OPEN.date(),
        settlement_session=OPEN.date(),
        market_calendar="XNYS",
    )
    repository = PriceRequestRepository(pool)
    await repository.register_request(request)
    pending = next(
        r for r in await repository.load_open_requests() if r.request_id == request.request_id
    )
    message = build_price_observed(pending, observation, observation)
    assert await repository.complete_request(message)
    assert not await repository.complete_request(message)
    assert await pool.fetchval("SELECT count(*) FROM market_data.close_observations") == before
    finance_row = await pool.fetchrow(
        "SELECT close,source,content_hash FROM market_data.close_observations "
        "WHERE asset_id=$1 AND session=$2 AND registry_version=$3",
        str(ASSET), OPEN.date(), SERIES.registry_version,
    )
    assert str(finance_row["close"]) == "99.50"
    assert finance_row["source"] == SERIES.provider
    assert finance_row["content_hash"] == "original-finance"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM market_data.outbox WHERE aggregate_id=$1", request.request_id
        )
        == 1
    )
    stored = await pool.fetchval(
        "SELECT payload FROM market_data.sampled_closes WHERE asset_id=$1 AND session=$2",
        str(ASSET),
        OPEN.date(),
    )
    assert json.loads(stored)["sample"]["quote_delay_seconds"] == 900


@pytest.mark.parametrize("avanza_enabled", [True, False])
@pytest.mark.parametrize("finance_fails_once", [True, False])
async def test_stream_fallback_replaces_samples_and_survives_restart(
    pool: Any,
    monkeypatch: pytest.MonkeyPatch,
    avanza_enabled: bool,
    finance_fails_once: bool,
) -> None:
    from shared.schemas.messages import IntradayBar

    import market_data.intraday as module

    await pool.execute(DDL)
    await pool.execute(DDL)
    request = IntradayRequested(
        stream_id=uuid4(),
        correlation_id=uuid4(),
        occurred_at=OPEN,
        asset_id=ASSET,
        registry_version=SERIES.registry_version,
        calendar_id="XNYS",
        opens_at=OPEN,
        closes_at=WINDOW.closes_at,
        price_policy="LAST_KNOWN_PRICE_V1",
    )
    avanza = SimpleNamespace(
        window=lambda *_: WINDOW, minutes=AsyncMock(side_effect=AvanzaUnavailable("MISSING_SLOT"))
    )
    finance = SimpleNamespace(
        fetch=AsyncMock(
            return_value=[IntradayBar(start=OPEN, open=99, high=101, low=98, close=100)]
        )
    )
    collector = IntradayCollector(pool, AsyncMock(), finance, MarketDataSettings(), avanza=avanza)
    await collector.register(request)
    if not avanza_enabled:
        collector.avanza = None
    if finance_fails_once:
        finance.fetch.side_effect = [RuntimeError("test outage"), finance.fetch.return_value,
                                    finance.fetch.return_value]
    original = minute_values([sample(0)], NOW)
    await pool.execute(
        "UPDATE market_data.intraday_streams SET bars=$2::jsonb,revision=1 WHERE stream_id=$1",
        request.stream_id,
        json.dumps({b.start.isoformat(): b.model_dump(mode="json") for b in original}),
    )
    lease = uuid4()
    await pool.execute(
        "UPDATE market_data.intraday_streams SET lease=$2 WHERE stream_id=$1",
        request.stream_id,
        lease,
    )
    row = await pool.fetchrow(
        "SELECT * FROM market_data.intraday_streams WHERE stream_id=$1", request.stream_id
    )

    class Clock:
        @staticmethod
        def now(tz: Any) -> datetime:
            return OPEN + timedelta(minutes=15)

    monkeypatch.setattr(module, "datetime", Clock)
    await collector.collect(row, lease)
    if finance_fails_once:
        failed = await pool.fetchrow(
            "SELECT * FROM market_data.intraday_streams WHERE stream_id=$1", request.stream_id
        )
        assert failed["source_mode"] == "finance"
        assert failed["last_error"] == "PROVIDER_UNAVAILABLE"
        assert failed["revision"] == 1  # No fabricated replacement on a failed finance read.
        await pool.execute(
            "UPDATE market_data.intraday_streams SET lease=$2 WHERE stream_id=$1",
            request.stream_id, lease,
        )
        failed = await pool.fetchrow(
            "SELECT * FROM market_data.intraday_streams WHERE stream_id=$1", request.stream_id
        )
        await collector.collect(failed, lease)
    saved = await pool.fetchrow(
        "SELECT * FROM market_data.intraday_streams WHERE stream_id=$1", request.stream_id
    )
    assert saved["source_mode"] == "finance"
    assert saved["fallback_reason"] == ("MISSING_SLOT" if avanza_enabled else "FINANCE_MODE")
    payload = await pool.fetchval(
        "SELECT payload FROM market_data.intraday_outbox WHERE payload::jsonb->>'stream_id'=$1",
        str(request.stream_id),
    )
    event = IntradayObserved.model_validate_json(payload)
    assert event.replaces_previous and event.source == "yahoo"
    assert len(event.bars) == 1 and event.bars[0].sample is None
    assert len(json.loads(saved["bars"])) == 1
    # A new collector instance must not switch back to Avanza inside this stream.
    await pool.execute(
        "UPDATE market_data.intraday_streams SET lease=$2 WHERE stream_id=$1",
        request.stream_id,
        lease,
    )
    row = await pool.fetchrow(
        "SELECT * FROM market_data.intraday_streams WHERE stream_id=$1", request.stream_id
    )
    restarted = IntradayCollector(pool, AsyncMock(), finance, MarketDataSettings(), avanza=avanza)
    await restarted.collect(row, lease)
    assert avanza.minutes.await_count == int(avanza_enabled)
    assert finance.fetch.await_count == 2 + int(finance_fails_once)
