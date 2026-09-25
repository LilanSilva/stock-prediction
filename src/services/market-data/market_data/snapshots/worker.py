"""Run with python -m market_data.snapshots.worker; independent of API readiness."""

from __future__ import annotations

import asyncio
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg
import structlog
from shared.logging import setup_logging
from shared.messaging.client import RabbitMQClient
from shared.messaging.snapshot_topology import ensure_snapshot_topology
from shared.schemas.messages import AssetId, PriceSampleObserved

from market_data.snapshots.browser import AvanzaBrowser, Quote, ReadFailure
from market_data.snapshots.calendar import Session, session_for
from market_data.snapshots.config import Listing, MappingFile, SnapshotSettings, load_mappings
from market_data.snapshots.storage import DDL, SnapshotStore

logger = structlog.get_logger(__name__)


def sample_message(row: Any, quote: Quote, settings: SnapshotSettings) -> PriceSampleObserved:
    session_window = Session.model_validate_json(row["session_window"])
    listing = Listing.model_validate_json(row["listing"])
    if quote.currency != listing.expected_currency:
        raise ReadFailure("CURRENCY_MISMATCH", pause=True)
    quality: Literal["FRESH", "STALE", "FRESHNESS_UNKNOWN", "SESSION_MISMATCH"]
    quality = "FRESHNESS_UNKNOWN"
    if quote.provider_quote_at:
        age = (quote.observed_at - quote.provider_quote_at).total_seconds()
        quality = "FRESH" if 0 <= age <= settings.max_quote_age_seconds else "STALE"
    if row["kind"] == "REGULAR" and (
        quote.market_state != "REGULAR_OPEN" or quote.observed_at >= session_window.closes_at
    ):
        quality = "SESSION_MISMATCH"
    return PriceSampleObserved(
        message_id=row["job_id"],
        sample_id=row["job_id"],
        correlation_id=row["job_id"],
        occurred_at=quote.observed_at,
        asset_id=AssetId(listing.asset_id),
        mapping_version=row["version"],
        registry_version=row["registry_version"],
        session=session_window.session,
        opens_at=session_window.opens_at,
        closes_at=session_window.closes_at,
        scheduled_at=row["scheduled_at"],
        observed_at=quote.observed_at,
        provider_quote_at=quote.provider_quote_at,
        quote_delay_seconds=quote.quote_delay_seconds,
        price=quote.price,
        currency=quote.currency,
        quote_unit=listing.quote_unit,
        kind=row["kind"],
        market_state=quote.market_state,
        quality=quality,
    )


class SnapshotWorker:
    def __init__(
        self, store: SnapshotStore, browser: AvanzaBrowser, settings: SnapshotSettings
    ) -> None:
        self.store, self.browser, self.settings = store, browser, settings
        self.config: MappingFile | None = None
        self.last_schedule: datetime | None = None
        self.restart_lock = asyncio.Lock()
        self.browser_restarted = False

    async def reload(self) -> None:
        candidate = load_mappings(self.settings.mappings_path)
        if not self.config or candidate.content_hash != self.config.content_hash:
            await self.store.install(candidate)
            self.config = candidate
            self.last_schedule = None

    async def collect(self, row: Any) -> None:
        now = datetime.now(UTC)
        started_at = now
        started_clock = time.monotonic()
        read_logger = logger.bind(
            asset_id=row["asset_id"],
            job_id=str(row["job_id"]),
            scheduled_at=row["scheduled_at"].isoformat(),
            started_at=started_at.isoformat(),
            attempt=row["attempts"],
        )
        if row["attempts"] > 2:
            await self.store.failure(row, "ATTEMPTS_EXHAUSTED", now)
            read_logger.warning("snapshot_read_skipped", reason="ATTEMPTS_EXHAUSTED")
            return
        deadline = row["scheduled_at"] + timedelta(seconds=self.settings.max_lateness_seconds)
        session_window = Session.model_validate_json(row["session_window"])
        if row["kind"] == "REGULAR":
            deadline = min(deadline, session_window.closes_at)
        if now >= deadline:
            await self.store.failure(row, "MISSED_DEADLINE", now)
            read_logger.warning("snapshot_read_skipped", reason="MISSED_DEADLINE")
            return
        try:
            listing = Listing.model_validate_json(row["listing"])
            try:
                registry_version = listing.check_registry()
            except (ValueError, KeyError) as exc:
                raise ReadFailure("REGISTRY_CHANGED", pause=True) from exc
            if registry_version != row["registry_version"]:
                raise ReadFailure("REGISTRY_CHANGED", pause=True)
            budget = min(self.settings.timeout_seconds, (deadline - now).total_seconds())
            quote = await asyncio.wait_for(self.browser.read(listing), timeout=budget)
            if quote.observed_at > deadline:
                raise ReadFailure("MISSED_DEADLINE")
            message = sample_message(row, quote, self.settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc if isinstance(exc, ReadFailure) else ReadFailure("FETCH_FAILED")
            now = datetime.now(UTC)
            if failure.provider:
                await self.store.control(
                    failure.code, now, pause=failure.pause, cooldown=failure.retry_after
                )
            retry_delay = self.settings.retry_seconds + random.uniform(0, 3)
            retry = (
                not failure.pause
                and not failure.provider
                and row["attempts"] < 2
                and now + timedelta(seconds=retry_delay + self.settings.timeout_seconds) < deadline
            )
            await self.store.failure(
                row, failure.code, now, retry=retry, pause=failure.pause, retry_seconds=retry_delay
            )
            if not retry and failure.code in ("FETCH_FAILED", "PROVIDER_UNAVAILABLE"):
                await self.store.provider_failure(now)
            if failure.code == "FETCH_FAILED":
                async with self.restart_lock:
                    if not self.browser_restarted:
                        self.browser_restarted = True
                        await self.browser.close()
            read_logger.warning(
                "snapshot_read_failed",
                reason=failure.code,
                error_type=type(exc).__name__,
                retry=retry,
                finished_at=now.isoformat(),
                wall_elapsed_seconds=round((now - started_at).total_seconds(), 3),
                elapsed_seconds=round(time.monotonic() - started_clock, 3),
            )
            return
        # Never reread a price after SQL failure and label it with the earlier read time.
        for attempt in range(2):
            try:
                if await self.store.save(row, message, quote.status_text):
                    read_logger.info(
                        "snapshot_sample_saved",
                        observed_at=message.observed_at.isoformat(),
                        currency=message.currency,
                        quality=message.quality,
                        elapsed_seconds=round(time.monotonic() - started_clock, 3),
                    )
                    await self.store.pool.execute(
                        "UPDATE market_data.snapshot_control SET failure_count=0 WHERE id=1"
                    )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt:
                    read_logger.error("snapshot_save_failed", error_type=type(exc).__name__)
                    return
                await asyncio.sleep(1)

    async def tick(self) -> None:
        now = datetime.now(UTC)
        config_error = False
        try:
            await self.reload()
        except (ValueError, OSError):
            config_error = True
            await self.store.control("CONFIG_ERROR", now)
            if not self.config:
                return
        assert self.config is not None
        await self.store.expire(now, self.settings.max_lateness_seconds)
        state = await self.store.pool.fetchrow(
            "SELECT * FROM market_data.snapshot_control WHERE id=1"
        )
        if state["paused"] or (state["cooldown_until"] and state["cooldown_until"] > now):
            await self.store.pool.execute(
                "UPDATE market_data.snapshot_control SET heartbeat=$1 WHERE id=1", now
            )
            return
        pending = await self.store.pool.fetchval(
            "SELECT count(*) FROM market_data.snapshot_outbox WHERE NOT delivered"
        )
        count = await self.store.pool.fetchval("SELECT count(*) FROM market_data.price_samples")
        if pending >= self.settings.max_pending_events or count >= self.settings.max_samples:
            await self.store.control("CAPACITY_PAUSED", now)
            return
        if not self.last_schedule or now - self.last_schedule >= timedelta(seconds=60):
            for listing in self.config.listings:
                if not listing.enabled:
                    continue
                try:
                    session_window = session_for(now, listing.calendar_id, listing.timezone)
                    await self.store.schedule(
                        listing, self.config.mapping_version, session_window, now
                    )
                except (ValueError, KeyError):
                    await self.store.pool.execute(
                        "UPDATE market_data.snapshot_assets SET paused=true,"
                        "last_error='CALENDAR_OR_REGISTRY_INVALID' WHERE asset_id=$1",
                        listing.asset_id,
                    )
            self.last_schedule = now
            await self.store.expire(now, self.settings.max_lateness_seconds)
        # A single probe after cooldown, then restore bounded normal concurrency on success.
        probing = bool(state["cooldown_until"])
        count = 1 if probing else self.settings.concurrency
        jobs = []
        for _ in range(count):
            row = await self.store.claim(now)
            if row is None:
                break
            jobs.append(row)
        self.browser_restarted = False
        results = await asyncio.gather(*(self.collect(row) for row in jobs), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                logger.error("snapshot_job_unavailable", reason=type(result).__name__)
        if probing and jobs:
            await self.store.pool.execute(
                "UPDATE market_data.snapshot_control SET cooldown_until=NULL "
                "WHERE id=1 AND failure_count=0 AND NOT paused"
            )
        await self.store.pool.execute(
            "UPDATE market_data.snapshot_control SET heartbeat=$1, "
            "status=CASE WHEN paused THEN status WHEN cooldown_until>$1 THEN 'COOLDOWN' "
            "WHEN $2 THEN 'CONFIG_ERROR' ELSE 'RUNNING' END WHERE id=1",
            datetime.now(UTC),
            config_error,
        )


async def relay(store: SnapshotStore, rabbit: RabbitMQClient, broker_url: str) -> None:
    if not rabbit.is_connected:
        await rabbit.close()
        await ensure_snapshot_topology(broker_url)
        await rabbit.connect()
    for row in await store.pool.fetch(
        "SELECT * FROM market_data.snapshot_outbox WHERE NOT delivered AND "
        "next_attempt_at<=now() ORDER BY created_at LIMIT 100"
    ):
        try:
            await rabbit.publish(PriceSampleObserved.model_validate_json(row["payload"]))
        except Exception:
            await store.pool.execute(
                "UPDATE market_data.snapshot_outbox SET attempts=attempts+1,"
                "next_attempt_at=now()+interval '30 seconds' WHERE message_id=$1",
                row["message_id"],
            )
            return
        await store.pool.execute(
            "UPDATE market_data.snapshot_outbox SET delivered=true WHERE message_id=$1",
            row["message_id"],
        )


async def run() -> None:
    settings = SnapshotSettings()
    setup_logging("market-data-snapshots", settings.log_level)
    if not settings.enabled:
        logger.info("snapshot_worker_disabled")
        return
    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=14, timeout=15, command_timeout=15
    )
    await pool.execute("CREATE SCHEMA IF NOT EXISTS market_data")
    await pool.execute(DDL)
    store = SnapshotStore(pool)
    browser = AvanzaBrowser(settings)
    rabbit = RabbitMQClient(settings.rabbitmq_url)
    worker = SnapshotWorker(store, browser, settings)
    try:
        # One leader coordinates provider-wide limits. Job leases fence crash/restart writes.
        async with pool.acquire() as leader:
            if not await leader.fetchval("SELECT pg_try_advisory_lock(734920151)"):
                logger.info("snapshot_worker_standby")
                return
            try:
                while True:
                    # Losing the dedicated lock connection must stop this worker's next cycle.
                    await leader.fetchval("SELECT 1")
                    try:
                        await worker.tick()
                    except Exception as exc:
                        logger.error("snapshot_tick_failed", error_type=type(exc).__name__)
                    try:
                        await asyncio.wait_for(
                            relay(store, rabbit, settings.rabbitmq_url), timeout=15
                        )
                    except Exception as exc:
                        logger.warning("snapshot_outbox_waiting", error_type=type(exc).__name__)
                    await asyncio.sleep(2)
            finally:
                await leader.execute("SELECT pg_advisory_unlock(734920151)")
    finally:
        await browser.close()
        await rabbit.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
