"""Opt-in real SQL tests. INTRADAY_TEST_DATABASE_URL must name a disposable database."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio
from shared.reference import AssetReferenceSeries
from shared.schemas.messages import IntradayBar, IntradayObserved, IntradayRequested, PredictionMade

from verification.config import VerificationSettings
from verification.db import SCHEMA_DDL
from verification.intraday import DDL, IntradayVerification
from verification.intraday_policy import SessionWindow

pytestmark = pytest.mark.integration
START = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=2)
WINDOW = SessionWindow(calendar_id="XNYS", opens_at=START, closes_at=START + timedelta(minutes=20))


@pytest_asyncio.fixture
async def pool() -> AsyncIterator[Any]:
    dsn = os.environ.get("INTRADAY_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires a disposable INTRADAY_TEST_DATABASE_URL")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    await pool.execute(SCHEMA_DDL)
    await pool.execute(DDL)
    await pool.execute(DDL)  # Startup migration must be repeatable.
    yield pool
    await pool.close()


@pytest.fixture
def settings() -> VerificationSettings:
    return VerificationSettings(intraday_mode="SHADOW", intraday_calendars={"TEST_STOCK": "XNYS"})


@pytest.fixture
def series(monkeypatch: pytest.MonkeyPatch) -> AssetReferenceSeries:
    value = AssetReferenceSeries(
        asset_id="TEST_STOCK",
        provider="yahoo",
        provider_symbol="TEST",
        economic_identity="test",
        expected_exchange="TEST",
        timezone="America/New_York",
        currency="USD",
        price_kind="PROVIDER_DAILY_CLOSE",
        is_adjusted=False,
        rollover_policy="test",
        fallback=None,
        registry_version=str(uuid.uuid4()),
        session_completion_hour=17,
        session_completion_minute=0,
        code="TEST:TEST",
        display_name="Test",
        group_id="TEST",
    )
    monkeypatch.setattr(
        "shared.schemas.asset_id.is_known_asset", lambda value: value == "TEST_STOCK"
    )
    monkeypatch.setattr("verification.intraday.resolve", lambda asset: value)
    monkeypatch.setattr("verification.intraday.resolve_window", lambda *args: WINDOW)
    return value


def prediction(**changes: Any) -> PredictionMade:
    values = dict(
        correlation_id=uuid.uuid4(),
        occurred_at=START,
        prediction_id=uuid.uuid4(),
        context_id=uuid.uuid4(),
        context_version=1,
        event_ids=[uuid.uuid4()],
        asset_id="TEST_STOCK",
        direction="UP",
        magnitude="SMALL",
        confidence=0.7,
        horizon="ONE_TRADING_DAY",
        rationale="test",
        decision_at=START,
        decision_method="GRAPH_ONLY",
    )
    values.update(changes)
    return PredictionMade(**values)


def bars() -> list[IntradayBar]:
    return [
        IntradayBar(
            start=START + timedelta(minutes=i),
            open=100,
            high=101 if i == 5 else 100,
            low=100,
            close=100,
        )
        for i in range(20)
    ]


async def request_for(pool: Any, pid: uuid.UUID) -> IntradayRequested:
    return IntradayRequested.model_validate_json(
        await pool.fetchval(
            "SELECT s.request FROM verification.intraday_streams s "
            "JOIN verification.intraday_evaluations e USING(stream_id) WHERE e.prediction_id=$1",
            pid,
        )
    )


def observed(
    request: IntradayRequested, revision: int, prices: list[IntradayBar], final: bool = False
) -> IntradayObserved:
    return IntradayObserved(
        correlation_id=request.correlation_id,
        occurred_at=datetime.now(UTC),
        stream_id=request.stream_id,
        asset_id=request.asset_id,
        registry_version=request.registry_version,
        revision=revision,
        bars=prices,
        final=final,
    )


async def test_duplicate_predictions_share_one_stream_and_outbox(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    first, second = prediction(), prediction()
    await verifier.register(first)
    await verifier.register(first)
    await verifier.register(second)
    request = await request_for(pool, first.prediction_id)
    assert (await request_for(pool, second.prediction_id)).stream_id == request.stream_id
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM verification.outbox_events WHERE aggregate_id=$1",
            request.stream_id,
        )
        == 1
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM verification.intraday_evaluations WHERE stream_id=$1",
            request.stream_id,
        )
        == 2
    )


async def test_out_of_order_final_waits_for_gap_and_restart_recovers(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    p = prediction()
    await verifier.register(p)
    request = await request_for(pool, p.prediction_id)
    await verifier.observe(observed(request, 2, bars()[10:], final=True))
    await verifier.score_one(p.prediction_id)
    assert (await report_for(verifier, p.prediction_id))["status"] == "PENDING"
    verifier = IntradayVerification(pool, settings)
    earlier = observed(request, 1, bars()[:10])
    await verifier.observe(earlier)
    await verifier.observe(earlier)
    await verifier.score_one(p.prediction_id)
    report = await report_for(verifier, p.prediction_id)
    assert report["status"] == "SCORED"
    assert report["result"]["target_reached"] is True
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM verification.outbox_events WHERE aggregate_id=$1 "
            "AND message_type='PredictionScored'",
            p.prediction_id,
        )
        == 0
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM verification.intraday_batches WHERE stream_id=$1",
            request.stream_id,
        )
        == 2
    )


async def test_supersession_before_original_message_is_durable(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    old = prediction(decision_at=START - timedelta(hours=2))
    replacement = prediction(
        decision_at=START - timedelta(hours=1), supersedes_prediction_id=old.prediction_id
    )
    await verifier.register(replacement)
    await verifier.register(old)
    await verifier.score_one(old.prediction_id)
    assert (await report_for(verifier, old.prediction_id))["status"] == "WITHDRAWN"


async def test_supersession_after_open_preserves_measurement(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    old = prediction()
    await verifier.register(old)
    await verifier.register(
        prediction(
            decision_at=START + timedelta(minutes=1), supersedes_prediction_id=old.prediction_id
        )
    )
    request = await request_for(pool, old.prediction_id)
    await verifier.observe(observed(request, 1, bars(), final=True))
    await verifier.score_one(old.prediction_id)
    assert (await report_for(verifier, old.prediction_id))["status"] == "SCORED"


async def test_identity_validation_orphan_and_off_mode(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    p = prediction()
    await verifier.register(p)
    request = await request_for(pool, p.prediction_id)
    with pytest.raises(ValueError, match="identity"):
        await verifier.observe(
            observed(request, 1, []).model_copy(update={"registry_version": "bad"})
        )
    await verifier.observe(observed(request, 1, []).model_copy(update={"stream_id": uuid.uuid4()}))
    off = IntradayVerification(pool, settings.model_copy(update={"intraday_mode": "OFF"}))
    ignored = prediction()
    await off.register(ignored)
    assert await off.report(ignored.prediction_id) is None


async def test_collector_delta_outbox_and_expired_lease(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_data.config import MarketDataSettings
    from market_data.intraday import DDL as MARKET_DDL
    from market_data.intraday import IntradayCollector

    await pool.execute("CREATE SCHEMA IF NOT EXISTS market_data")
    await pool.execute(MARKET_DDL)
    await pool.execute(MARKET_DDL)
    monkeypatch.setattr("market_data.intraday.resolve", lambda asset: series)
    verifier = IntradayVerification(pool, settings)
    p = prediction()
    await verifier.register(p)
    request = await request_for(pool, p.prediction_id)
    publisher = AsyncMock()
    adapter = AsyncMock()
    adapter.fetch.return_value = bars()[:10]
    collector = IntradayCollector(pool, publisher, adapter, MarketDataSettings())
    await collector.register(request)
    await collector.register(request)
    await collector.tick()
    sent = publisher.publish.call_args.args[0]
    assert sent.revision == 1 and len(sent.bars) == 10 and not sent.final
    # Simulate a worker dying after claiming; the lease expires and another instance recovers.
    await pool.execute(
        "UPDATE market_data.intraday_streams SET lease=$2, "
        "next_attempt_at=now()-interval '1 second' "
        "WHERE stream_id=$1",
        request.stream_id,
        uuid.uuid4(),
    )
    adapter.fetch.return_value = bars()
    await collector.tick()
    sent = publisher.publish.call_args.args[0]
    assert sent.revision == 2 and len(sent.bars) == 10 and sent.final
    row = await pool.fetchrow(
        "SELECT * FROM market_data.intraday_streams WHERE stream_id=$1", request.stream_id
    )
    assert row["done"]
    assert len(json.loads(row["bars"])) == 20


async def report_for(verifier: IntradayVerification, pid: uuid.UUID) -> dict[str, Any]:
    result = await verifier.report(pid)
    assert result is not None
    return cast(dict[str, Any], result)


async def test_late_preopen_withdrawal_updates_terminal_result(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    old = prediction(decision_at=START - timedelta(hours=2))
    await verifier.register(old)
    request = await request_for(pool, old.prediction_id)
    await verifier.observe(observed(request, 1, bars(), final=True))
    await verifier.score_one(old.prediction_id)
    assert (await report_for(verifier, old.prediction_id))["status"] == "SCORED"
    await verifier.register(
        prediction(
            decision_at=START - timedelta(hours=1), supersedes_prediction_id=old.prediction_id
        )
    )
    assert (await report_for(verifier, old.prediction_id))["status"] == "WITHDRAWN"


async def test_replay_freezes_policy_and_conflicting_revision_is_rejected(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
) -> None:
    verifier = IntradayVerification(pool, settings)
    p = prediction()
    await verifier.register(p)
    original = await report_for(verifier, p.prediction_id)
    changed = IntradayVerification(
        pool, settings.model_copy(update={"intraday_target_return": 0.02})
    )
    await changed.register(p)
    assert (await report_for(changed, p.prediction_id))["policy_hash"] == original["policy_hash"]
    request = await request_for(pool, p.prediction_id)
    await verifier.observe(observed(request, 1, bars()))
    with pytest.raises(ValueError, match="conflicting"):
        await verifier.observe(observed(request, 1, bars()[:10]))
    assert await verifier.summary()


async def test_collector_deadline_finishes_without_provider_call(
    pool: Any,
    series: AssetReferenceSeries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_data.config import MarketDataSettings
    from market_data.intraday import DDL as MARKET_DDL
    from market_data.intraday import IntradayCollector

    await pool.execute("CREATE SCHEMA IF NOT EXISTS market_data")
    await pool.execute(MARKET_DDL)
    monkeypatch.setattr("market_data.intraday.resolve", lambda asset: series)
    start = START - timedelta(days=3)
    request = IntradayRequested(
        correlation_id=uuid.uuid4(),
        occurred_at=start,
        stream_id=uuid.uuid4(),
        asset_id="TEST_STOCK",
        registry_version=series.registry_version,
        calendar_id="XNYS",
        opens_at=start,
        closes_at=start + timedelta(minutes=20),
    )
    publisher, adapter = AsyncMock(), AsyncMock()
    collector = IntradayCollector(pool, publisher, adapter, MarketDataSettings())
    await collector.register(request)
    await collector.tick()
    adapter.fetch.assert_not_awaited()
    message = publisher.publish.call_args.args[0]
    assert message.final and message.failure == "DATA_DEADLINE_EXPIRED"


async def test_collector_publish_failure_keeps_committed_evidence(
    pool: Any,
    settings: VerificationSettings,
    series: AssetReferenceSeries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from market_data.config import MarketDataSettings
    from market_data.intraday import DDL as MARKET_DDL
    from market_data.intraday import IntradayCollector

    await pool.execute("CREATE SCHEMA IF NOT EXISTS market_data")
    await pool.execute(MARKET_DDL)
    monkeypatch.setattr("market_data.intraday.resolve", lambda asset: series)
    verifier = IntradayVerification(pool, settings)
    p = prediction()
    await verifier.register(p)
    request = await request_for(pool, p.prediction_id)
    publisher, adapter = AsyncMock(), AsyncMock()
    publisher.publish.side_effect = RuntimeError("test broker failure")
    adapter.fetch.return_value = bars()
    collector = IntradayCollector(pool, publisher, adapter, MarketDataSettings())
    await collector.register(request)
    await collector.tick()
    failed = publisher.publish.call_args.args[0]
    assert not await pool.fetchval(
        "SELECT delivered FROM market_data.intraday_outbox WHERE message_id=$1", failed.message_id
    )
    publisher.publish.side_effect = None
    await collector.tick()
    assert publisher.publish.call_args.args[0].message_id == failed.message_id
    adapter.fetch.assert_awaited_once()
