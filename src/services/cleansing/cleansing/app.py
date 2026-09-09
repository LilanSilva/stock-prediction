"""Cleansing Service FastAPI application.

Wires the pipeline to the `cleansing.articles` consumer, runs a periodic cluster-close + outbox
sweep inside the FastAPI lifespan, exposes `/health` (liveness) and `/ready` (PostgreSQL, RabbitMQ,
and embedding backend), and shuts down gracefully.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg
import structlog
from aio_pika.abc import AbstractIncomingMessage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from shared.llm.adapters import build_provider
from shared.llm.gateway import LLMGateway
from shared.llm.settings import LLMSettings
from shared.logging import setup_logging
from shared.messaging.client import ConsumerCallback, RabbitMQClient
from shared.messaging.exceptions import MessagePoisonError
from shared.schemas.messages import ArticleIngested

from cleansing.classify import LlmClassifier
from cleansing.config import CleansingSettings
from cleansing.db import apply_schema, create_pool
from cleansing.embedding import BgeM3Embedder, Embedder, build_embedder
from cleansing.extraction import ActionExtractor, SpacyExtractor, build_extractor
from cleansing.merge import LlmMerger
from cleansing.outbox import EventOutboxPublisher
from cleansing.pipeline import CleansingPipeline
from cleansing.repository import CleansingRepository

logger = structlog.get_logger(__name__)


@dataclass
class ServiceState:
    last_close_at: datetime | None = None
    last_events_produced: int = 0
    last_published: int = 0
    processed: int = 0
    per_type_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class AppContext:
    settings: CleansingSettings
    pool: asyncpg.Pool
    rabbit: RabbitMQClient
    embedder: Embedder
    scheduler: AsyncIOScheduler
    pipeline: CleansingPipeline
    outbox: EventOutboxPublisher
    consumer_task: asyncio.Task[None]
    state: ServiceState


def _build_llm_gateway(settings: CleansingSettings) -> LLMGateway | None:
    """Construct the shared LLM gateway once, for both the merger and the classifier below.

    Any configuration problem (missing provider/key, or an unsupported provider) degrades the
    service to local-only processing rather than failing startup: the LLM is optional and used only
    for ambiguous clusters and OTHER-typed articles (functional document sec 6).
    """
    if not settings.llm_enabled:
        return None
    llm_settings = LLMSettings()
    try:
        llm_settings.require_configured()
        llm_settings.require_api_key()
        provider = build_provider(llm_settings)
    except Exception as exc:  # noqa: BLE001 - unconfigured/unsupported LLM => local-only mode
        logger.info("llm_disabled", reason=str(exc))
        return None
    return LLMGateway(llm_settings, {provider.name: provider})


def _build_llm_merger(settings: CleansingSettings, gateway: LLMGateway | None) -> LlmMerger | None:
    if gateway is None:
        return None
    return LlmMerger(
        gateway,
        prompt_version=settings.llm_prompt_version,
        max_excerpts=settings.llm_max_excerpts,
        excerpt_chars=settings.llm_excerpt_chars,
    )


def _build_llm_classifier(
    settings: CleansingSettings, gateway: LLMGateway | None
) -> LlmClassifier | None:
    """Construct the OTHER-only classification fallback (see `cleansing.classify`).

    Gated on its own flag in addition to the shared gateway, so the classify fallback can be turned
    off independently of the merge step (e.g. to measure the deterministic-only accuracy floor).
    """
    if gateway is None or not settings.llm_classify_other:
        return None
    return LlmClassifier(
        gateway,
        prompt_version=settings.llm_classify_prompt_version,
        body_chars=settings.llm_classify_body_chars,
    )


async def _run_close(app: FastAPI) -> None:
    ctx: AppContext = app.state.ctx
    try:
        produced = await ctx.pipeline.close_ready_clusters()
        published = await ctx.outbox.publish_pending()
    except Exception:  # noqa: BLE001 - a scheduled sweep must never crash the scheduler
        logger.exception("scheduled_close_failed")
        return
    ctx.state.last_close_at = datetime.now(UTC)
    ctx.state.last_events_produced = produced
    ctx.state.last_published = published


def _make_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            article = ArticleIngested.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            # Invalid schema is unrecoverable: dead-letter with validation metadata.
            raise MessagePoisonError(f"invalid ArticleIngested: {exc}") from exc
        await ctx.pipeline.process_article(article)
        ctx.state.processed += 1

    return _on_message


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = CleansingSettings()
    setup_logging("cleansing", settings.log_level)

    pool = await create_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    await apply_schema(pool)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    embedder = build_embedder(
        settings.embedding_backend,
        dimension=settings.embedding_dimension,
        bge_model_name=settings.bge_model_name,
    )
    if isinstance(embedder, BgeM3Embedder):
        embedder.load()  # load once at startup so /ready reflects real availability

    llm_gateway = _build_llm_gateway(settings)
    extractor: ActionExtractor = build_extractor(
        settings.nlp_backend, _build_llm_classifier(settings, llm_gateway)
    )
    if isinstance(extractor, SpacyExtractor):
        extractor.load()

    repository = CleansingRepository(pool)
    outbox = EventOutboxPublisher(pool, rabbit)
    pipeline = CleansingPipeline(
        repository,
        embedder,
        extractor,
        settings,
        llm_merger=_build_llm_merger(settings, llm_gateway),
    )

    # Reconcile any outbox rows left pending by a previous crash before starting new work.
    await outbox.publish_pending()

    consumer_task = asyncio.create_task(
        rabbit.consume(settings.articles_queue, _make_consumer(app))
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_close,
        "interval",
        seconds=settings.close_interval_seconds,
        args=[app],
        id="cluster_close_sweep",
        max_instances=1,
        coalesce=True,
        # A sweep may run long when it makes bounded LLM merge calls; without a generous grace the
        # default 1 s misfire window silently skips every slightly-late tick, stalling the backlog.
        misfire_grace_time=settings.close_interval_seconds,
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()

    app.state.ctx = AppContext(
        settings=settings,
        pool=pool,
        rabbit=rabbit,
        embedder=embedder,
        scheduler=scheduler,
        pipeline=pipeline,
        outbox=outbox,
        consumer_task=consumer_task,
        state=ServiceState(),
    )
    logger.info(
        "cleansing_started",
        queue=settings.articles_queue,
        embedding_backend=settings.embedding_backend,
        nlp_backend=settings.nlp_backend,
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
        await rabbit.close()
        await pool.close()
        logger.info("cleansing_stopped")


app = FastAPI(title="Feed Analyzer Cleansing", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness plus last-sweep visibility."""
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "processed": state.processed,
        "last_close_at": state.last_close_at.isoformat() if state.last_close_at else None,
        "last_events_produced": state.last_events_produced,
        "last_published": state.last_published,
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: PostgreSQL reachable, RabbitMQ connected, embedding backend ready."""
    ctx: AppContext = app.state.ctx
    checks: dict[str, bool] = {}
    try:
        await ctx.pool.fetchval("SELECT 1")
        checks["postgres"] = True
    except Exception:  # noqa: BLE001 - readiness probe never raises
        checks["postgres"] = False
    checks["rabbitmq"] = ctx.rabbit.is_connected
    checks["embedder"] = ctx.embedder.is_ready()

    ok = all(checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
