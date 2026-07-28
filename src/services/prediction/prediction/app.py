"""FastAPI application wiring for the Prediction Service.

Lifespan brings up the Postgres pool, the RabbitMQ client, and the shared Neo4j causal-graph client,
starts the ``prediction.events`` consumer and the APScheduler context-close sweep, and exposes
``/health`` and ``/ready``. M1 makes no LLM calls and never publishes ``PriceRequested``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import structlog
from aio_pika.abc import AbstractIncomingMessage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from shared.graph import CausalGraphClient, Neo4jSettings
from shared.logging import setup_logging
from shared.messaging.client import ConsumerCallback, RabbitMQClient
from shared.messaging.exceptions import MessagePoisonError
from shared.schemas.messages import EventDetected

from prediction.config import PredictionSettings
from prediction.db import apply_schema, create_pool
from prediction.outbox import PredictionOutboxPublisher
from prediction.pipeline import PredictionPipeline
from prediction.repository import PredictionRepository

logger = structlog.get_logger(__name__)


@dataclass
class ServiceState:
    processed: int = 0
    last_close_at: datetime | None = None
    last_predictions_produced: int = 0
    last_published: int = 0


@dataclass
class AppContext:
    settings: PredictionSettings
    pool: asyncpg.Pool
    rabbit: RabbitMQClient
    graph: CausalGraphClient
    scheduler: AsyncIOScheduler
    pipeline: PredictionPipeline
    outbox: PredictionOutboxPublisher
    consumer_task: asyncio.Task[None]
    state: ServiceState


async def _run_close(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        produced = await ctx.pipeline.close_ready_contexts()
        published = await ctx.outbox.publish_pending()
    except Exception:  # noqa: BLE001 - a scheduled sweep must never crash the scheduler
        logger.exception("scheduled_close_failed")
        return
    ctx.state.last_close_at = datetime.now(UTC)
    ctx.state.last_predictions_produced = produced
    ctx.state.last_published = published


def _make_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            event = EventDetected.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            # Invalid schema is unrecoverable: dead-letter with validation metadata.
            raise MessagePoisonError(f"invalid EventDetected: {exc}") from exc
        await ctx.pipeline.process_event(event)
        ctx.state.processed += 1

    return _on_message


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = PredictionSettings()
    setup_logging("prediction", settings.log_level)

    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    await apply_schema(pool)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    graph = CausalGraphClient(Neo4jSettings())
    await graph.connect()

    repository = PredictionRepository(pool)
    outbox = PredictionOutboxPublisher(pool, rabbit)
    pipeline = PredictionPipeline(repository, graph, settings)

    # Reconcile any outbox rows left pending by a previous crash before starting new work.
    await outbox.publish_pending()

    consumer_task = asyncio.create_task(
        rabbit.consume(settings.events_queue, _make_consumer(app))
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_close,
        "interval",
        seconds=settings.close_interval_seconds,
        args=[app],
        id="context_close_sweep",
        max_instances=1,
        coalesce=True,
        # Generous grace so a slightly-late sweep still runs instead of being silently skipped.
        misfire_grace_time=settings.close_interval_seconds,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()

    app.state.ctx = AppContext(
        settings=settings,
        pool=pool,
        rabbit=rabbit,
        graph=graph,
        scheduler=scheduler,
        pipeline=pipeline,
        outbox=outbox,
        consumer_task=consumer_task,
        state=ServiceState(),
    )
    logger.info(
        "prediction_started",
        queue=settings.events_queue,
        context_window_minutes=settings.context_window_minutes,
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
        await graph.close()
        await rabbit.close()
        await pool.close()
        logger.info("prediction_stopped")


app = FastAPI(title="Feed Analyzer Prediction", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus last-sweep visibility."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "processed": state.processed,
        "last_close_at": state.last_close_at.isoformat() if state.last_close_at else None,
        "last_predictions_produced": state.last_predictions_produced,
        "last_published": state.last_published,
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: PostgreSQL reachable, RabbitMQ connected, Neo4j reachable."""
    ctx: AppContext = app.state.ctx
    checks: dict[str, bool] = {}
    try:
        await ctx.pool.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception:  # noqa: BLE001 - readiness probe never raises
        checks["postgres"] = False
    checks["rabbitmq"] = ctx.rabbit.is_connected
    checks["neo4j"] = await ctx.graph.verify_connectivity()

    ok = all(checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
