"""FastAPI application wiring for the Verification Service.

Lifespan brings up the Postgres pool and RabbitMQ client, starts the two consumers
(``verification.predictions`` and ``verification.prices``) and the APScheduler outbox-relay sweep,
and exposes ``/health`` and ``/ready``. No Neo4j and no LLM.
"""

from __future__ import annotations

import asyncio
import json
import uuid
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
from shared.messaging.intraday_topology import ensure_intraday_topology
from shared.messaging.snapshot_topology import ensure_snapshot_topology
from shared.schemas.messages import (
    IntradayObserved,
    PredictionMade,
    PriceObserved,
    PriceSampleObserved,
)

from verification.config import VerificationSettings
from verification.db import apply_schema, create_pool
from verification.exceptions import (
    InvalidPredictionError,
    OrphanedObservationError,
    PriceValidationError,
)
from verification.intraday import DDL as INTRADAY_DDL
from verification.intraday import IntradayVerification
from verification.outbox import VerificationOutboxPublisher
from verification.pipeline import VerificationPipeline
from verification.repository import VerificationRepository
from verification.sampled import DDL as SAMPLED_DDL
from verification.sampled import SampleVerification

logger = structlog.get_logger(__name__)


@dataclass
class ServiceState:
    predictions_seen: int = 0
    prices_seen: int = 0
    # Observations acknowledged without scoring because their evaluation row was gone. Surfaced on
    # /health so the condition is visible without a growing dead-letter queue to notice it by.
    prices_orphaned: int = 0
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
    intraday: IntradayVerification | None = None
    sampled: SampleVerification | None = None


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
            if ctx.intraday:
                await ctx.intraday.register(prediction)
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
        except OrphanedObservationError as exc:
            # Acknowledged, not dead-lettered. The evaluation row this observation needs is gone, so
            # no retry can succeed and the message itself holds no evidence a human could act on —
            # the missing row is the evidence. Dead-lettering it filled the DLQ with unactionable
            # traffic and drowned out the mismatches that DO need inspection.
            #
            # Same treatment as a WITHDRAWN evaluation, which is also "nothing to score"; the
            # difference is the log level, because an orphan is unexpected in production. It is
            # routine in local testing, where an integration test deletes its own predictions and
            # evaluations while a PriceObserved for them is still in flight.
            ctx.state.prices_orphaned += 1
            logger.warning(
                "price_observed_orphaned",
                request_id=str(observed.request_id),
                prediction_id=str(observed.prediction_id),
                asset_id=observed.asset_id.value,
                orphaned_total=ctx.state.prices_orphaned,
                reason=str(exc),
            )
            return
        except PriceValidationError as exc:
            raise MessagePoisonError(str(exc)) from exc
        ctx.state.prices_seen += 1

    return _on_message


def _make_intraday_consumer(app: FastAPI) -> ConsumerCallback:
    async def consume(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            observed = IntradayObserved.model_validate_json(message.body)
            if ctx.intraday:
                await ctx.intraday.observe(observed)
        except ValueError as exc:
            raise MessagePoisonError(str(exc)) from exc

    return consume


async def _run_intraday(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        if ctx.intraday:
            await ctx.intraday.tick()
    except Exception:
        logger.exception("intraday_verification_tick_failed")


def _make_sample_consumer(app: FastAPI, *, predictions: bool = False) -> ConsumerCallback:
    async def consume(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        if not ctx.sampled:
            return
        try:
            if predictions:
                await ctx.sampled.register(PredictionMade.model_validate_json(message.body))
            else:
                await ctx.sampled.observe(PriceSampleObserved.model_validate_json(message.body))
        except ValueError as exc:
            raise MessagePoisonError(str(exc)) from exc
    return consume


async def _run_samples(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        if ctx.sampled:
            await ctx.sampled.tick()
    except Exception:
        logger.exception("sample_verification_tick_failed")


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
    await pool.execute(INTRADAY_DDL)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    repository = VerificationRepository(pool)
    outbox = VerificationOutboxPublisher(pool, rabbit)
    pipeline = VerificationPipeline(repository, settings)
    intraday = None
    sampled = None
    if settings.sample_mode == "SHADOW":
        await pool.execute(SAMPLED_DDL)
        await ensure_snapshot_topology(settings.rabbitmq_url)
        sampled = SampleVerification(pool, settings)
    if settings.intraday_mode == "SHADOW":
        await ensure_intraday_topology(settings.rabbitmq_url)
        intraday = IntradayVerification(pool, settings)

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
    if sampled:
        consumer_tasks.extend(
            [
                asyncio.create_task(
                    rabbit.consume("verification.price-samples", _make_sample_consumer(app))
                ),
                asyncio.create_task(
                    rabbit.consume(
                        "verification.sample-predictions",
                        _make_sample_consumer(app, predictions=True),
                    )
                ),
            ]
        )
        scheduler.add_job(
            _run_samples, "interval", seconds=30, args=[app],
            id="sample_verification", max_instances=1, coalesce=True,
        )
    if intraday:
        consumer_tasks.append(asyncio.create_task(
            rabbit.consume(settings.intraday_prices_queue, _make_intraday_consumer(app))
        ))
        scheduler.add_job(
            _run_intraday, "interval", seconds=settings.intraday_poll_seconds,
            args=[app], id="intraday_verification", max_instances=1, coalesce=True,
        )
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
        intraday=intraday,
        sampled=sampled,
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


@app.get("/verification/sampled/{prediction_id}")
async def sampled_result(prediction_id: uuid.UUID) -> JSONResponse:
    ctx: AppContext = app.state.ctx
    if not ctx.sampled:
        return JSONResponse({"mode": "OFF"}, status_code=503)
    result = await ctx.sampled.get(prediction_id)
    if result is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return JSONResponse({
        "prediction_id": str(prediction_id), "mode": "SHADOW", "status": result["status"],
        "policy": json.loads(str(result["policy"])),
        "window": json.loads(str(result["session_window"])),
        "result": json.loads(str(result["result"])) if result["result"] else None,
    })


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus last-sweep visibility."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "predictions_seen": state.predictions_seen,
        "prices_seen": state.prices_seen,
        "prices_orphaned": state.prices_orphaned,
        "last_sweep_at": state.last_sweep_at.isoformat() if state.last_sweep_at else None,
        "last_published": state.last_published,
        "intraday_mode": ctx.settings.intraday_mode,
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
    if ctx.intraday:
        checks["intraday_consumers"] = all(not task.done() for task in ctx.consumer_tasks)

    ok = all(checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)


@app.get("/verification/intraday/{prediction_id}")
async def intraday_report(prediction_id: uuid.UUID) -> JSONResponse:
    ctx: AppContext = app.state.ctx
    report = await IntradayVerification(ctx.pool, ctx.settings).report(prediction_id)
    return JSONResponse(report or {"detail": "No intraday evaluation"},
                        status_code=200 if report else 404)


@app.get("/verification/intraday")
async def intraday_summary() -> dict[str, object]:
    ctx: AppContext = app.state.ctx
    return {"mode": ctx.settings.intraday_mode,
            "cohorts": await IntradayVerification(ctx.pool, ctx.settings).summary()}
