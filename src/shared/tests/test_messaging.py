import asyncio
import os
import uuid
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aio_pika
import pytest
import structlog
from aio_pika.abc import AbstractIncomingMessage

from shared.messaging.client import RETRY_COUNT_HEADER, RabbitMQClient
from shared.messaging.exceptions import (
    MessagePoisonError,
    MessagePublishError,
    RabbitMQConnectionError,
)
from shared.schemas.messages import EXCHANGE, RoutingKey
from tests.factories import make_article


class _FakeExchange:
    def __init__(self) -> None:
        self.publish = AsyncMock()


class _FakePoolCtx:
    def __init__(self, channel: object) -> None:
        self._channel = channel

    async def __aenter__(self) -> object:
        return self._channel

    async def __aexit__(self, *args: object) -> None:
        return None


def _client_with_channel(channel: object) -> RabbitMQClient:
    client = RabbitMQClient("amqp://x")
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(channel))
    client._channel_pool = pool
    return client


async def test_publish_uses_topic_exchange_and_canonical_routing_key() -> None:
    exchange = _FakeExchange()
    channel = MagicMock()
    channel.get_exchange = AsyncMock(return_value=exchange)
    client = _client_with_channel(channel)

    await client.publish(make_article())

    channel.get_exchange.assert_awaited_once()
    assert channel.get_exchange.call_args.args[0] == EXCHANGE
    exchange.publish.assert_awaited_once()
    assert exchange.publish.call_args.kwargs["routing_key"] == RoutingKey.ARTICLE_INGESTED.value
    published = exchange.publish.call_args.args[0]
    assert published.delivery_mode is aio_pika.DeliveryMode.PERSISTENT


async def test_publish_before_connect_raises() -> None:
    client = RabbitMQClient("amqp://x")
    with pytest.raises(RabbitMQConnectionError):
        await client.publish(make_article())


async def test_publish_unknown_type_raises() -> None:
    channel = MagicMock()
    client = _client_with_channel(channel)

    class NotAMessage:
        message_id = "x"

    with pytest.raises(MessagePublishError):
        await client.publish(NotAMessage())  # type: ignore[arg-type]


def _incoming(headers: dict[str, Any] | None = None) -> MagicMock:
    msg = MagicMock()
    msg.headers = headers or {}
    msg.body = b"{}"
    msg.content_type = "application/json"
    msg.message_id = "mid"
    msg.correlation_id = "cid"
    msg.ack = AsyncMock()
    msg.nack = AsyncMock()
    return msg


async def test_successful_callback_acks() -> None:
    client = RabbitMQClient("amqp://x")
    msg = _incoming()
    await client._process_message("q", msg, AsyncMock())
    msg.ack.assert_awaited_once()
    msg.nack.assert_not_called()


async def test_message_context_is_bound_for_callback_then_cleared() -> None:
    client = RabbitMQClient("amqp://x")
    msg = _incoming()
    observed_context: dict[str, object] = {}

    async def callback(_: object) -> None:
        observed_context.update(structlog.contextvars.get_contextvars())

    await client._process_message("q", msg, callback)

    assert observed_context["message_id"] == "mid"
    assert observed_context["correlation_id"] == "cid"
    assert structlog.contextvars.get_contextvars() == {}


async def test_poison_error_dead_letters_immediately() -> None:
    client = RabbitMQClient("amqp://x")
    msg = _incoming()

    async def cb(_: object) -> None:
        raise MessagePoisonError("bad")

    await client._process_message("q", msg, cb)
    msg.nack.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_called()


async def test_transient_error_republishes_with_incremented_retry() -> None:
    channel = MagicMock()
    channel.default_exchange = MagicMock()
    channel.default_exchange.publish = AsyncMock()
    client = _client_with_channel(channel)
    client._max_retries = 3
    msg = _incoming(headers={RETRY_COUNT_HEADER: 0})

    async def cb(_: object) -> None:
        raise RuntimeError("transient")

    await client._process_message("q", msg, cb)

    channel.default_exchange.publish.assert_awaited_once()
    published = channel.default_exchange.publish.call_args.args[0]
    assert published.headers[RETRY_COUNT_HEADER] == 1
    msg.ack.assert_awaited_once()  # original acked after republish


async def test_transient_error_dead_letters_after_max_retries() -> None:
    client = RabbitMQClient("amqp://x")
    client._max_retries = 3
    msg = _incoming(headers={RETRY_COUNT_HEADER: 3})

    async def cb(_: object) -> None:
        raise RuntimeError("transient")

    await client._process_message("q", msg, cb)
    msg.nack.assert_awaited_once_with(requeue=False)


@pytest.mark.integration
async def test_publish_consume_roundtrip_live() -> None:
    url = os.environ.get("RABBITMQ_URL")
    if not url:
        pytest.skip("RABBITMQ_URL not set; integration test requires live RabbitMQ (S01)")

    setup_connection = await aio_pika.connect_robust(url)
    setup_channel = await setup_connection.channel()
    exchange = await setup_channel.get_exchange(EXCHANGE, ensure=True)
    # Not exclusive: RabbitMQClient consumes over its own connection, and exclusive queues are
    # bound to their declaring connection (cross-connection access raises RESOURCE_LOCKED).
    queue = await setup_channel.declare_queue(
        f"test.shared.{uuid.uuid4()}",
        exclusive=False,
        auto_delete=True,
    )
    await queue.bind(exchange, RoutingKey.ARTICLE_INGESTED.value)

    article = make_article()
    received = asyncio.Event()
    try:
        async with RabbitMQClient(url) as client:
            async def handler(message: AbstractIncomingMessage) -> None:
                restored = type(article).from_amqp_body(message.body)
                if restored.message_id == article.message_id:
                    received.set()

            consume_task = asyncio.create_task(client.consume(queue.name, handler))
            # Cancel the consumer while the client (and its channel pool) is still open, so
            # cancellation cleanup does not race the pool being closed on context exit.
            try:
                await client.publish(article)
                await asyncio.wait_for(received.wait(), timeout=10)
            finally:
                consume_task.cancel()
                with suppress(asyncio.CancelledError):
                    await consume_task
    finally:
        await setup_connection.close()

    assert received.is_set()
