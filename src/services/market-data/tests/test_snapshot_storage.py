"""Opt-in PostgreSQL tests; use only an empty disposable database named snapshot_test."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest
from shared.schemas.messages import PriceSampleObserved

from market_data.db import SCHEMA_DDL
from market_data.snapshots.browser import Quote
from market_data.snapshots.calendar import Session
from market_data.snapshots.config import MappingFile
from market_data.snapshots.storage import DDL, SnapshotStore, status
from market_data.snapshots.worker import relay, sample_message
from market_data.storage import get_recent_closes
from tests.test_snapshots import listing as listing
from tests.test_snapshots import settings as settings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def pool() -> Any:
    url = os.environ.get("SNAPSHOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("SNAPSHOT_TEST_DATABASE_URL not configured")
    db = await asyncpg.create_pool(url, min_size=1, max_size=4)
    assert await db.fetchval("SELECT current_database()") == "snapshot_test"
    await db.execute("CREATE SCHEMA IF NOT EXISTS market_data")
    await db.execute(DDL)
    await db.execute(DDL)
    await db.execute(
        "TRUNCATE market_data.snapshot_mappings,market_data.snapshot_assets,"
        "market_data.snapshot_sessions,market_data.snapshot_jobs,"
        "market_data.price_samples,market_data.snapshot_outbox CASCADE"
    )
    yield db
    await db.close()


async def test_restart_leases_atomic_outbox_and_broker_replay(
    pool: Any, listing: Any, settings: Any
) -> None:
    now = datetime.now(UTC)
    window = Session(
        session=now.date(),
        opens_at=now,
        closes_at=now + timedelta(hours=1),
        next_open_at=now + timedelta(days=1),
    )
    config = MappingFile(mapping_version="v1", listings=[listing])
    store = SnapshotStore(pool)
    await pool.execute(SCHEMA_DDL)
    await pool.execute("DELETE FROM market_data.close_observations WHERE asset_id='TEST_STOCK'")
    await pool.execute(
        "INSERT INTO market_data.close_observations "
        "(asset_id,session,close,fetched_at,source,provider_symbol,price_kind,"
        "is_adjusted,registry_version,content_hash) "
        "VALUES('TEST_STOCK',$1,99.50,$2,'test','TEST','PROVIDER_DAILY_CLOSE',false,'r1','test')",
        now.date(),
        now,
    )
    daily_before = await get_recent_closes(pool, listing.asset_id, 20)
    await store.install(config)
    await store.install(config)
    await store.schedule(listing, "v1", window, now)
    restarted = SnapshotStore(pool)
    await restarted.schedule(listing, "v1", window, now)
    assert await pool.fetchval("SELECT count(*) FROM market_data.snapshot_jobs") == 7
    claims = await asyncio.gather(store.claim(now), restarted.claim(now))
    assert sum(row is not None for row in claims) == 1
    old = next(row for row in claims if row is not None)
    await pool.execute(
        "UPDATE market_data.snapshot_jobs SET lease_until=$1 WHERE job_id=$2",
        now - timedelta(seconds=1),
        old["job_id"],
    )
    row = await restarted.claim(now)
    quote = Quote(
        price=Decimal("103.45"),
        currency="USD",
        observed_at=now,
        market_state="REGULAR_OPEN",
        status_text="open",
    )
    message = sample_message(row, quote, settings)
    assert not await store.save(old, message, "open")
    assert await restarted.save(row, message, "open")
    assert not await restarted.save(row, message, "open")
    stored = await pool.fetchrow("SELECT * FROM market_data.price_samples")
    assert stored["price"] == Decimal("103.45")
    assert PriceSampleObserved.model_validate_json(stored["payload"]) == message
    assert await get_recent_closes(pool, listing.asset_id, 20) == daily_before
    assert daily_before == [(now.date(), Decimal("99.50"))]
    rabbit = AsyncMock()
    rabbit.is_connected = True
    rabbit.publish.side_effect = [ConnectionError(), None]
    await relay(store, rabbit, "unused")
    assert (
        await pool.fetchval("SELECT count(*) FROM market_data.snapshot_outbox WHERE NOT delivered")
        == 1
    )
    await pool.execute("UPDATE market_data.snapshot_outbox SET next_attempt_at=now()")
    await relay(restarted, rabbit, "unused")
    assert rabbit.publish.await_args_list[0].args == rabbit.publish.await_args_list[1].args
    assert (
        await pool.fetchval("SELECT count(*) FROM market_data.snapshot_outbox WHERE delivered") == 1
    )
    assert (await status(pool))["pending_events"] == 0
    with pytest.raises(ValueError, match="version change"):
        await store.install(config.model_copy(update={"listings": []}))
    assert await pool.fetchval("SELECT enabled FROM market_data.snapshot_assets") is True


async def test_mapping_change_fences_old_jobs_and_preserves_currency_history(
    pool: Any, listing: Any, settings: Any
) -> None:
    now = datetime.now(UTC)
    window = Session(
        session=now.date(),
        opens_at=now,
        closes_at=now + timedelta(hours=1),
        next_open_at=now + timedelta(days=1),
    )
    store = SnapshotStore(pool)
    await store.install(MappingFile(mapping_version="v1", listings=[listing]))
    await store.schedule(listing, "v1", window, now)
    row = await store.claim(now)
    quote = Quote(
        price=Decimal("100"),
        currency="USD",
        observed_at=now,
        market_state="REGULAR_OPEN",
        status_text="open",
    )
    await store.install(MappingFile(mapping_version="v2", listings=[listing]))
    assert not await store.save(row, sample_message(row, quote, settings), "open")
    assert await pool.fetchval("SELECT count(*) FROM market_data.price_samples") == 0
    assert await pool.fetchval("SELECT count(*) FROM market_data.snapshot_mappings") == 2
    await store.schedule(listing, "v2", window, now)
    row = await store.claim(now)
    assert await store.save(row, sample_message(row, quote, settings), "open")
    await store.expire(window.closes_at + timedelta(minutes=10), 120)
    assert await store.claim(window.next_open_at) is None
    assert (
        await pool.fetchval("SELECT close_status FROM market_data.snapshot_sessions LIMIT 1")
        == "CLOSE_UNCONFIRMED"
    )
