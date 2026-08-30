"""Market Data Service FastAPI application.

Wiring:
  - Consumes `market-data.price-requests` (PriceRequested). On delivery the request is persisted as
    PENDING before the message is acknowledged, then processing is attempted immediately.
  - An APScheduler job re-drives open (pending / baseline-observed) requests when their settlement
    session completes, and reconciles the outbox.
  - On startup, pending work and publish-pending outbox rows are rehydrated; in-memory scheduling
    alone is insufficient.
  - Exposes `/health` (liveness) and `/ready` (PostgreSQL, RabbitMQ, scheduler, registry entries).
  - Shuts down gracefully: stops new consumption, stops the scheduler, closes clients.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

import asyncpg
import httpx
import structlog
from aio_pika.abc import AbstractIncomingMessage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from shared.logging import setup_logging
from shared.messaging.client import RabbitMQClient
from shared.messaging.exceptions import MessagePoisonError
from shared.reference import supported_assets
from shared.schemas.messages import AssetId, PriceRequested

from market_data.adapters.biquote import BiquoteAdapter
from market_data.adapters.router import AdapterRouter
from market_data.adapters.yahoo import YahooAdapter
from market_data.config import MarketDataSettings
from market_data.db import apply_schema, create_pool
from market_data.exceptions import InvalidObservationError
from market_data.handler import PriceRequestProcessor
from market_data.storage import (
    MAX_RECENT_SESSIONS,
    MIN_RECENT_SESSIONS,
    OutboxPublisher,
    PriceRequestRepository,
    get_recent_closes,
)

logger = structlog.get_logger(__name__)


class RecentClose(BaseModel):
    """A single immutable session close. `close` serializes as a string to avoid float rounding."""

    session: date
    close: Decimal


class RecentClosesResponse(BaseModel):
    asset_id: AssetId
    closes: list[RecentClose]



@dataclass
class ServiceState:
    last_poll_at: datetime | None = None
    # Requests due on the last tick, not the total still open: deferred ones are excluded.
    due_requests: int = 0
    last_published: int = 0
    consumed_total: int = 0


@dataclass
class AppContext:
    settings: MarketDataSettings
    pool: asyncpg.Pool
    rabbit: RabbitMQClient
    http: httpx.AsyncClient
    scheduler: AsyncIOScheduler
    repository: PriceRequestRepository
    processor: PriceRequestProcessor
    outbox: OutboxPublisher
    state: ServiceState
    consumer_task: asyncio.Task[None] | None = field(default=None)


async def _handle_price_request(app: FastAPI, message: AbstractIncomingMessage) -> None:
    """Consumer callback: persist the request durably, then attempt processing."""
    ctx: AppContext = app.state.ctx
    try:
        request_msg = PriceRequested.model_validate_json(message.body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - unparseable body is a poison message
        raise MessagePoisonError(f"invalid PriceRequested body: {exc}") from exc

    # Persist before ack. The shared client acks only after this callback returns normally.
    await ctx.repository.register_request(request_msg)
    ctx.state.consumed_total += 1

    open_requests = await ctx.repository.load_open_requests()
    target = next((r for r in open_requests if r.request_id == request_msg.request_id), None)
    if target is None:
        return
    try:
        outcome = await ctx.processor.process(target)
    except InvalidObservationError as exc:
        # Terminal provider-data error: the request stays recorded but this delivery dead-letters.
        raise MessagePoisonError(str(exc)) from exc
    if outcome.published:
        await ctx.outbox.publish_pending()


async def _run_scheduler_tick(app: FastAPI) -> None:
    """Re-drive open requests and reconcile the outbox."""
    ctx: AppContext = app.state.ctx
    try:
        open_requests = await ctx.repository.load_open_requests()
        now = datetime.now(UTC)
        for request in open_requests:
            try:
                await ctx.processor.process(request, now=now)
            except InvalidObservationError:
                logger.exception("scheduled_process_terminal", request_id=str(request.request_id))
        published = await ctx.outbox.publish_pending()
        ctx.state.last_poll_at = now
        ctx.state.due_requests = len(open_requests)
        ctx.state.last_published = published
    except Exception:  # noqa: BLE001 - a scheduled tick must never crash the scheduler
        logger.exception("scheduled_tick_failed")


async def _consume_loop(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx

    async def callback(message: AbstractIncomingMessage) -> None:
        await _handle_price_request(app, message)

    await ctx.rabbit.consume(ctx.settings.price_requests_queue, callback)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = MarketDataSettings()
    setup_logging("market-data", settings.log_level)

    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    await apply_schema(pool)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    http = httpx.AsyncClient(timeout=settings.provider_timeout_seconds)
    # One adapter per provider; the registry decides which one prices each asset.
    adapter = AdapterRouter(
        {
            "biquote.io": BiquoteAdapter(
                http,
                base_url=settings.biquote_base_url,
                fetch_window_days=settings.fetch_window_days,
            ),
            "yahoo": YahooAdapter(
                http,
                base_url=settings.yahoo_base_url,
                fetch_window_days=settings.fetch_window_days,
            ),
        }
    )
    repository = PriceRequestRepository(pool)
    processor = PriceRequestProcessor(
        repository,
        adapter,
        retry_backoff_base_seconds=settings.retry_backoff_base_seconds,
        retry_backoff_max_seconds=settings.retry_backoff_max_seconds,
        abandon_after_settlement_days=settings.abandon_after_settlement_days,
    )
    outbox = OutboxPublisher(pool, rabbit)

    # Rehydrate durable work left by a previous run before scheduling or consuming anything new.
    await outbox.publish_pending()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduler_tick,
        "interval",
        seconds=settings.settlement_poll_interval_seconds,
        args=[app],
        id="settlement_poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,  # tolerate up to 5 min event-loop lag (e.g. host display-off throttle)
        next_run_time=datetime.now(UTC),  # reconcile pending work shortly after startup
    )
    scheduler.start()

    app.state.ctx = AppContext(
        settings=settings,
        pool=pool,
        rabbit=rabbit,
        http=http,
        scheduler=scheduler,
        repository=repository,
        processor=processor,
        outbox=outbox,
        state=ServiceState(),
    )
    consumer_task = asyncio.create_task(_consume_loop(app))
    app.state.ctx.consumer_task = consumer_task
    logger.info("market_data_started", queue=settings.price_requests_queue)
    try:
        yield
    finally:
        consumer_task.cancel()
        try:
            await consumer_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown best-effort
            pass
        scheduler.shutdown(wait=False)
        await http.aclose()
        await rabbit.close()
        await pool.close()
        logger.info("market_data_stopped")


app = FastAPI(title="Feed Analyzer Market Data", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus last-poll visibility."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "last_poll_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
        "due_requests": state.due_requests,
        "last_published": state.last_published,
        "consumed_total": state.consumed_total,
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: PostgreSQL reachable, RabbitMQ connected, scheduler running, registry present."""
    ctx: AppContext = app.state.ctx
    checks: dict[str, bool] = {}
    try:
        await ctx.pool.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception:  # noqa: BLE001 - readiness probe never raises
        checks["postgres"] = False
    checks["rabbitmq"] = ctx.rabbit.is_connected
    checks["scheduler"] = ctx.scheduler.running
    checks["registry"] = len(supported_assets()) > 0

    ok = all(checks.values())
    status_code = 200 if ok else 503
    return JSONResponse({"ready": ok, "checks": checks}, status_code=status_code)


@app.get("/prices/recent")
async def prices_recent(
    asset_id: Annotated[AssetId, Query(description="Canonical asset id (e.g. GOLD, BRENT_OIL).")],
    sessions: Annotated[
        int, Query(ge=MIN_RECENT_SESSIONS, le=MAX_RECENT_SESSIONS)
    ] = 20,
) -> RecentClosesResponse:
    """Read-only recent closes for an asset. Canonical id only; provider symbols never leave here.

    An unknown `asset_id` or out-of-range `sessions` is rejected by request validation (HTTP 422).
    """
    ctx: AppContext = app.state.ctx
    pairs = await get_recent_closes(ctx.pool, asset_id, sessions)
    return RecentClosesResponse(
        asset_id=asset_id,
        closes=[RecentClose(session=session, close=close) for session, close in pairs],
    )

