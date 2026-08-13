"""FastAPI application wiring for the Credibility Service.

Lifespan brings up the Postgres pool, the RabbitMQ client, and the shared Neo4j causal-graph client,
starts the ``credibility.scored`` consumer, and exposes ``/health`` and ``/ready``. The service
publishes nothing — its output is the write-back to Neo4j edge weights and Postgres source scores.
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
from shared.schemas.messages import PredictionScored

from credibility.config import CredibilitySettings
from credibility.db import apply_schema, create_pool
from credibility.exceptions import InvalidScoredMessageError
from credibility.learning.config import LearningSettings
from credibility.learning.run import run_with as run_learning
from credibility.pipeline import CredibilityPipeline
from credibility.repository import CredibilityRepository

logger = structlog.get_logger(__name__)


@dataclass
class ServiceState:
    scored_seen: int = 0
    scored_applied: int = 0
    scored_duplicate: int = 0


@dataclass
class AppContext:
    settings: CredibilitySettings
    pool: asyncpg.Pool
    rabbit: RabbitMQClient
    graph: CausalGraphClient
    pipeline: CredibilityPipeline
    consumer_task: asyncio.Task[None]
    scheduler: AsyncIOScheduler | None
    state: ServiceState


def _make_scored_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            scored = PredictionScored.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            raise MessagePoisonError(f"invalid PredictionScored: {exc}") from exc
        try:
            applied = await ctx.pipeline.process(scored)
        except InvalidScoredMessageError as exc:
            raise MessagePoisonError(str(exc)) from exc
        ctx.state.scored_seen += 1
        if applied:
            ctx.state.scored_applied += 1
        else:
            ctx.state.scored_duplicate += 1

    return _on_message


async def _run_learning(app: FastAPI) -> None:
    """Scheduled offline structure-learning pass, reusing the service pool + graph."""
    ctx: AppContext = app.state.ctx
    try:
        written = await run_learning(ctx.pool, ctx.graph, LearningSettings())
        logger.info("learning_sweep_done", edges_written=written)
    except Exception:  # noqa: BLE001 - a scheduled learner must never crash the scheduler
        logger.exception("learning_sweep_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = CredibilitySettings()
    setup_logging("credibility", settings.log_level)

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

    repository = CredibilityRepository(pool)
    pipeline = CredibilityPipeline(
        repository,
        graph,
        prior_floor=settings.prior_floor,
        weight_step=settings.weight_step,
        weight_floor=settings.weight_floor,
    )

    consumer_task = asyncio.create_task(
        rabbit.consume(settings.scored_queue, _make_scored_consumer(app))
    )

    scheduler: AsyncIOScheduler | None = None
    if settings.learning_enabled:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            _run_learning,
            "interval",
            hours=settings.learning_interval_hours,
            args=[app],
            id="structure_learning_sweep",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            next_run_time=datetime.now(UTC),
        )

    app.state.ctx = AppContext(
        settings=settings,
        pool=pool,
        rabbit=rabbit,
        graph=graph,
        pipeline=pipeline,
        consumer_task=consumer_task,
        scheduler=scheduler,
        state=ServiceState(),
    )
    if scheduler is not None:
        scheduler.start()
    logger.info(
        "credibility_started",
        scored_queue=settings.scored_queue,
        learning_enabled=settings.learning_enabled,
    )
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
        await graph.close()
        await rabbit.close()
        await pool.close()
        logger.info("credibility_stopped")


app = FastAPI(title="Feed Analyzer Credibility", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus processing counters."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "scored_seen": state.scored_seen,
        "scored_applied": state.scored_applied,
        "scored_duplicate": state.scored_duplicate,
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
