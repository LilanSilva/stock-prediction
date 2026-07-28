"""FastAPI application wiring for the Verification Service.

Lifespan brings up the Postgres pool and RabbitMQ client, starts the two consumers
(``verification.predictions`` and ``verification.prices``) and the APScheduler outbox-relay sweep,
and exposes ``/health`` and ``/ready``. No Neo4j and no LLM.
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
from shared.logging import setup_logging
from shared.messaging.client import ConsumerCallback, RabbitMQClient
from shared.messaging.exceptions import MessagePoisonError
from shared.schemas.messages import PredictionMade, PriceObserved

from verification.config import VerificationSettings
from verification.db import apply_schema, create_pool
from verification.exceptions import InvalidPredictionError, PriceValidationError
from verification.outbox import VerificationOutboxPublisher
from verification.pipeline import VerificationPipeline
from verification.repository import VerificationRepository

logger = structlog.get_logger(__name__)


@dataclass
class ServiceState:
    predictions_seen: int = 0
    prices_seen: int = 0
    last_sweep_at: datetime | None = None
    last_published: int = 0


@dataclass
class AppContext:
    settings: VerificationSettings
    pool: asyncpg.Pool
    rabbit: RabbitMQClient
    scheduler: AsyncIOScheduler
    pipeline: VerificationPipeline
    outbox: VerificationOutboxPublisher
    consumer_tasks: list[asyncio.Task[None]]
    state: ServiceState


async def _run_sweep(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        published = await ctx.outbox.publish_pending()
    except Exception:  # noqa: BLE001 - a scheduled sweep must never crash the scheduler
        logger.exception("scheduled_sweep_failed")
        return
    ctx.state.last_sweep_at = datetime.now(UTC)
    ctx.state.last_published = published


def _make_prediction_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            prediction = PredictionMade.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            raise MessagePoisonError(f"invalid PredictionMade: {exc}") from exc
        try:
            await ctx.pipeline.process_prediction(prediction)
        except InvalidPredictionError as exc:
            raise MessagePoisonError(str(exc)) from exc
        ctx.state.predictions_seen += 1

    return _on_message


def _make_price_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            observed = PriceObserved.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            raise MessagePoisonError(f"invalid PriceObserved: {exc}") from exc
        try:
            await ctx.pipeline.process_price(observed)
        except PriceValidationError as exc:
            raise MessagePoisonError(str(exc)) from exc
        ctx.state.prices_seen += 1

    return _on_message


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = VerificationSettings()
    setup_logging("verification", settings.log_level)

    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    await apply_schema(pool)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    repository = VerificationRepository(pool)
    outbox = VerificationOutboxPublisher(pool, rabbit)
    pipeline = VerificationPipeline(repository, settings)

    # Reconcile any outbox rows left pending by a previous crash before starting new work.
    await outbox.publish_pending()

    consumer_tasks = [
        asyncio.create_task(
            rabbit.consume(settings.predictions_queue, _make_prediction_consumer(app))
        ),
        asyncio.create_task(
            rabbit.consume(settings.prices_queue, _make_price_consumer(app))
        ),
    ]

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_sweep,
        "interval",
        seconds=settings.outbox_interval_seconds,
        args=[app],
        id="verification_outbox_sweep",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=settings.outbox_interval_seconds,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()

    app.state.ctx = AppContext(
        settings=settings,
        pool=pool,
        rabbit=rabbit,
        scheduler=scheduler,
        pipeline=pipeline,
        outbox=outbox,
        consumer_tasks=consumer_tasks,
        state=ServiceState(),
    )
    logger.info(
        "verification_started",
        predictions_queue=settings.predictions_queue,
        prices_queue=settings.prices_queue,
    )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        for task in consumer_tasks:
            task.cancel()
        for task in consumer_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await rabbit.close()
        await pool.close()
        logger.info("verification_stopped")


app = FastAPI(title="Feed Analyzer Verification", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus last-sweep visibility."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "predictions_seen": state.predictions_seen,
        "prices_seen": state.prices_seen,
        "last_sweep_at": state.last_sweep_at.isoformat() if state.last_sweep_at else None,
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

    ok = all(checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
