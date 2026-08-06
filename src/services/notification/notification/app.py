"""FastAPI application wiring for the Notification Service.

Lifespan brings up the RabbitMQ client, loads recipient files, builds the channel list, and starts
the ``notification.predictions`` consumer. Exposes ``/health`` and ``/ready``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import asyncpg
import structlog
from aio_pika.abc import AbstractIncomingMessage
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import AsyncClient
from pydantic import ValidationError
from shared.logging import setup_logging
from shared.messaging.client import ConsumerCallback, RabbitMQClient
from shared.messaging.exceptions import MessagePoisonError
from shared.schemas.messages import PredictionMade, PredictionScored

from notification.channels.base import NotificationChannel
from notification.channels.email_channel import EmailChannel
from notification.channels.recipients import load_email_recipients, load_whatsapp_recipients
from notification.channels.whatsapp_channel import WhatsAppChannel
from notification.config import NotificationSettings
from notification.db import create_pool
from notification.engine import NotificationEngine

logger = structlog.get_logger(__name__)


@dataclass
class ServiceState:
    processed: int = 0
    dispatched: int = 0
    skipped_low_confidence: int = 0
    active_channels: list[str] = field(default_factory=list)


@dataclass
class AppContext:
    settings: NotificationSettings
    rabbit: RabbitMQClient
    http_client: AsyncClient
    engine: NotificationEngine
    consumer_task: asyncio.Task[None]
    scored_consumer_task: asyncio.Task[None]
    state: ServiceState
    db_pool: asyncpg.Pool | None = None


def _build_channels(
    settings: NotificationSettings,
    http_client: AsyncClient,
) -> list[NotificationChannel]:
    channels: list[NotificationChannel] = []

    if settings.brevo_api_key and settings.brevo_sender_email:
        recipients = load_email_recipients(settings.recipients_dir)
        if recipients:
            channels.append(
                EmailChannel(
                    api_key=settings.brevo_api_key,
                    sender_email=settings.brevo_sender_email,
                    sender_name=settings.brevo_sender_name,
                    recipients=recipients,
                    http_client=http_client,
                )
            )
            logger.info("email_channel_registered", recipient_count=len(recipients))
        else:
            logger.warning("email_channel_skipped_no_recipients")
    else:
        logger.warning("email_channel_disabled_missing_config")

    if settings.meta_access_token and settings.meta_phone_number_id:
        recipients = load_whatsapp_recipients(settings.recipients_dir)
        if recipients:
            channels.append(
                WhatsAppChannel(
                    access_token=settings.meta_access_token,
                    phone_number_id=settings.meta_phone_number_id,
                    api_version=settings.meta_api_version,
                    recipients=recipients,
                    http_client=http_client,
                )
            )
            logger.info("whatsapp_channel_registered", recipient_count=len(recipients))
        else:
            logger.warning("whatsapp_channel_skipped_no_recipients")
    else:
        logger.warning("whatsapp_channel_disabled_missing_config")

    return channels


def _make_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            prediction = PredictionMade.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            raise MessagePoisonError(f"invalid PredictionMade: {exc}") from exc

        was_below_threshold = prediction.confidence < ctx.settings.min_confidence
        await ctx.engine.handle(prediction)
        ctx.state.processed += 1
        if was_below_threshold:
            ctx.state.skipped_low_confidence += 1
        else:
            ctx.state.dispatched += 1

    return _on_message


def _make_scored_consumer(app: FastAPI) -> ConsumerCallback:
    async def _on_scored_message(message: AbstractIncomingMessage) -> None:
        ctx: AppContext = app.state.ctx
        try:
            scored = PredictionScored.model_validate_json(message.body.decode("utf-8"))
        except ValidationError as exc:
            raise MessagePoisonError(f"invalid PredictionScored: {exc}") from exc

        await ctx.engine.handle_scored(scored)
        ctx.state.processed += 1
        ctx.state.dispatched += 1

    return _on_scored_message


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = NotificationSettings()
    setup_logging("notification", settings.log_level)

    rabbit = RabbitMQClient(settings.rabbitmq_url)
    await rabbit.connect()

    http_client = AsyncClient(timeout=settings.channel_timeout_seconds)

    db_pool: asyncpg.Pool | None = None
    if settings.database_url:
        try:
            db_pool = await create_pool(settings.database_url)
            logger.info("notification_db_connected")
        except Exception as exc:  # noqa: BLE001
            logger.warning("notification_db_unavailable", error=str(exc))

    channels = _build_channels(settings, http_client)

    engine = NotificationEngine(
        channels=channels,
        min_confidence=settings.min_confidence,
        channel_timeout_seconds=settings.channel_timeout_seconds,
        db_pool=db_pool,
    )

    state = ServiceState(active_channels=[ch.channel_id for ch in channels])

    consumer_task = asyncio.create_task(
        rabbit.consume(settings.predictions_queue, _make_consumer(app))
    )
    scored_consumer_task = asyncio.create_task(
        rabbit.consume(settings.scores_queue, _make_scored_consumer(app))
    )

    app.state.ctx = AppContext(
        settings=settings,
        rabbit=rabbit,
        http_client=http_client,
        engine=engine,
        consumer_task=consumer_task,
        scored_consumer_task=scored_consumer_task,
        state=state,
        db_pool=db_pool,
    )
    logger.info(
        "notification_started",
        predictions_queue=settings.predictions_queue,
        scores_queue=settings.scores_queue,
        channels=state.active_channels,
        min_confidence=settings.min_confidence,
    )
    try:
        yield
    finally:
        consumer_task.cancel()
        scored_consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
        try:
            await scored_consumer_task
        except asyncio.CancelledError:
            pass
        await http_client.aclose()
        await rabbit.close()
        if db_pool is not None:
            await db_pool.close()
        logger.info("notification_stopped")


app = FastAPI(title="Feed Analyzer Notification", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    ctx: AppContext = app.state.ctx
    state = ctx.state
    return {
        "status": "ok",
        "processed": state.processed,
        "dispatched": state.dispatched,
        "skipped_low_confidence": state.skipped_low_confidence,
        "active_channels": state.active_channels,
    }


@app.get("/ready")
async def ready() -> JSONResponse:
    ctx: AppContext = app.state.ctx
    checks: dict[str, bool] = {
        "rabbitmq": ctx.rabbit.is_connected,
    }
    ok = all(checks.values())
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
