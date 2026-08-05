"""Ingestion Service FastAPI application.

Wires the pipeline into an APScheduler hourly job inside the FastAPI lifespan, exposes `/health`
(liveness) and `/ready` (PostgreSQL + RabbitMQ), and shuts down gracefully.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg
import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from shared.logging import setup_logging
from shared.messaging.client import RabbitMQClient

from ingestion.adapters.freenewsapi import FreeNewsApiAdapter
from ingestion.adapters.rss import SWEDISH_SOURCES, RssAdapter
from ingestion.config import IngestionSettings
from ingestion.db import apply_schema, create_pool
from ingestion.fetcher import BodyFetcher
from ingestion.pipeline import IngestionPipeline, SourceAdapter
from ingestion.resilience import wrap_with_breakers
from ingestion.retention import RetentionCleaner
from ingestion.storage import ArticleRepository, OutboxPublisher

logger = structlog.get_logger(__name__)


@dataclass
class PollState:
    last_poll_at: datetime | None = None
    per_source_counts: dict[str, int] = field(default_factory=dict)
    consecutive_empty_polls: int = 0
    last_published: int = 0


@dataclass
class AppContext:
    settings: IngestionSettings
    pool: asyncpg.Pool
    rabbit: RabbitMQClient
    http: httpx.AsyncClient
    scheduler: AsyncIOScheduler
    pipeline: IngestionPipeline
    retention: RetentionCleaner
    state: PollState


async def _run_poll(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        result = await ctx.pipeline.run_once()
    except Exception:  # noqa: BLE001 - a scheduled poll must never crash the scheduler
        logger.exception("scheduled_poll_failed")
        return
    ctx.state.last_poll_at = datetime.now(UTC)
    ctx.state.per_source_counts = result.per_source_counts
    ctx.state.last_published = result.published
    ctx.state.consecutive_empty_polls = (
        ctx.state.consecutive_empty_polls + 1 if result.total_new == 0 else 0
    )


async def _run_retention(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        await ctx.retention.run()
    except Exception:  # noqa: BLE001 - a scheduled cleanup must never crash the scheduler
        logger.exception("scheduled_retention_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = IngestionSettings()
    setup_logging("ingestion", settings.log_level)

    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    await apply_schema(pool)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    http = httpx.AsyncClient(timeout=settings.feed_fetch_timeout_seconds)
    # Each source is wrapped in its own circuit breaker so a flaky source fails fast in isolation.
    raw_adapters: list[SourceAdapter] = [RssAdapter(source, http) for source in SWEDISH_SOURCES]
    # FreeNewsApi is only added when a key is configured; without one the four RSS feeds still run.
    if settings.freenewsapi_key:
        raw_adapters.append(
            FreeNewsApiAdapter(
                http,
                api_key=settings.freenewsapi_key,
                base_url=settings.freenewsapi_base_url,
                language=settings.freenewsapi_language,
                page_size=settings.freenewsapi_page_size,
            )
        )
    else:
        logger.warning("freenewsapi_disabled_no_key")
    adapters = wrap_with_breakers(
        raw_adapters,
        failure_threshold=settings.circuit_failure_threshold,
        reset_timeout_seconds=settings.circuit_reset_timeout_seconds,
    )
    body_fetcher = (
        BodyFetcher(
            http,
            max_bytes=settings.body_max_chars * 4,
            overall_timeout_seconds=settings.body_fetch_overall_timeout_seconds,
        )
        if settings.body_fetch_enabled
        else None
    )
    repository = ArticleRepository(pool)
    outbox = OutboxPublisher(pool, rabbit)
    retention = RetentionCleaner(
        pool,
        article_retention_days=settings.article_retention_days,
        outbox_retention_days=settings.outbox_retention_days,
    )
    pipeline = IngestionPipeline(
        adapters,
        repository,
        outbox,
        body_fetcher=body_fetcher,
        body_fetch_enabled=settings.body_fetch_enabled,
        body_fetch_concurrency=settings.body_fetch_concurrency,
        max_body_chars=settings.body_max_chars,
    )

    # Reconcile any outbox rows left pending by a previous crash before scheduling new work.
    await outbox.publish_pending()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_poll,
        "interval",
        seconds=settings.poll_interval_seconds,
        args=[app],
        id="hourly_poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,  # tolerate up to 5 min event-loop lag (e.g. host display-off throttle)
        next_run_time=datetime.now(UTC),  # poll once shortly after startup, then on the interval
    )
    if settings.retention_enabled:
        scheduler.add_job(
            _run_retention,
            "interval",
            seconds=settings.retention_interval_seconds,
            args=[app],
            id="retention_cleanup",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
    scheduler.start()

    app.state.ctx = AppContext(
        settings=settings,
        pool=pool,
        rabbit=rabbit,
        http=http,
        scheduler=scheduler,
        pipeline=pipeline,
        retention=retention,
        state=PollState(),
    )
    logger.info("ingestion_started", sources=[a.source_id for a in adapters])
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await http.aclose()
        await rabbit.close()
        await pool.close()
        logger.info("ingestion_stopped")


app = FastAPI(title="Feed Analyzer Ingestion", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus last-poll visibility."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "last_poll_at": state.last_poll_at.isoformat() if state.last_poll_at else None,
        "per_source_counts": state.per_source_counts,
        "consecutive_empty_polls": state.consecutive_empty_polls,
        "last_published": state.last_published,
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: PostgreSQL reachable and RabbitMQ connected."""
    ctx: AppContext = app.state.ctx
    checks: dict[str, bool] = {}
    try:
        await ctx.pool.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception:  # noqa: BLE001 - readiness probe never raises
        checks["postgres"] = False
    checks["rabbitmq"] = ctx.rabbit.is_connected

    status_code = 200 if all(checks.values()) else 503
    return JSONResponse({"ready": all(checks.values()), "checks": checks}, status_code=status_code)
