"""Async RabbitMQ client wrapper (aio-pika).

Contract alignment (docs/contracts/message-contracts.md, contract-freeze-overrides.md E01):
  - Publish by ROUTING KEY to the durable topic exchange `feed.events`. This wrapper never publishes
    directly to a queue's default exchange, and never to a legacy per-service work queue.
  - Consume an explicitly-owned queue by name. Queues, the exchange, DLX and bindings are declared
    by infra/rabbitmq/definitions.json, so the wrapper only looks them up (never declares them).
  - Failed messages dead-letter via nack(requeue=False). Transient failures are retried up to
    max_retries by republishing with an incremented `x-retry-count` header, then dead-lettered.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

import aio_pika
import structlog
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
)
from aio_pika.pool import Pool

from shared.logging import bind_correlation_id
from shared.messaging.exceptions import (
    MessagePoisonError,
    MessagePublishError,
    RabbitMQConnectionError,
)
from shared.schemas.messages import EXCHANGE, ROUTING_KEY_BY_MESSAGE, FeedMessage

ConsumerCallback = Callable[[AbstractIncomingMessage], Awaitable[None]]

RETRY_COUNT_HEADER = "x-retry-count"


class RabbitMQClient:
    """Publish/consume wrapper over a robust aio-pika connection with a channel pool."""

    def __init__(
        self,
        url: str,
        *,
        max_retries: int = 3,
        prefetch_count: int = 10,
        channel_pool_size: int = 10,
    ) -> None:
        self._url = url
        self._max_retries = max_retries
        self._prefetch_count = prefetch_count
        self._channel_pool_size = channel_pool_size
        self._connection: AbstractRobustConnection | None = None
        self._channel_pool: Pool[AbstractRobustChannel] | None = None

    async def connect(self) -> None:
        """Establish a robust connection and initialize the channel pool."""
        try:
            self._connection = await aio_pika.connect_robust(self._url)
        except Exception as exc:  # noqa: BLE001 - surfaced as a typed domain error
            raise RabbitMQConnectionError(f"cannot connect to RabbitMQ: {exc}") from exc

        async def _new_channel() -> AbstractRobustChannel:
            assert self._connection is not None
            return await self._connection.channel()  # type: ignore[return-value]

        self._channel_pool = Pool(_new_channel, max_size=self._channel_pool_size)

    async def close(self) -> None:
        """Close the channel pool and the connection gracefully."""
        if self._channel_pool is not None:
            await self._channel_pool.close()
            self._channel_pool = None
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def _require_pool(self) -> Pool[AbstractRobustChannel]:
        if self._channel_pool is None:
            raise RabbitMQConnectionError("client is not connected; call connect() first")
        return self._channel_pool

    async def publish(self, message: FeedMessage, *, priority: int = 0) -> None:
        """Publish a FeedMessage to `feed.events` using its canonical routing key.

        The routing key is derived from the message type, so callers cannot accidentally route a
        message to the wrong binding. delivery_mode is PERSISTENT and the envelope identifiers are
        mirrored into AMQP properties for broker-side inspection.
        """
        routing_key = ROUTING_KEY_BY_MESSAGE.get(type(message))
        if routing_key is None:
            raise MessagePublishError(
                f"no canonical routing key registered for {type(message).__name__}"
            )

        amqp_message = aio_pika.Message(
            body=message.to_amqp_body(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            message_id=str(message.message_id),
            correlation_id=str(message.correlation_id),
            timestamp=message.occurred_at,
            priority=priority,
            headers={"schema_version": message.schema_version},
        )

        pool = self._require_pool()
        try:
            async with pool.acquire() as channel:
                exchange = await channel.get_exchange(EXCHANGE, ensure=True)
                await exchange.publish(amqp_message, routing_key=routing_key.value)
        except Exception as exc:  # noqa: BLE001 - surfaced as a typed domain error
            raise MessagePublishError(
                f"failed to publish {type(message).__name__} to {routing_key.value}: {exc}"
            ) from exc

    async def consume(self, queue_name: str, callback: ConsumerCallback) -> None:
        """Consume the named (already-declared) queue, dispatching each message to `callback`.

        ack/nack policy:
          - callback returns normally           -> ack
          - callback raises MessagePoisonError   -> nack(requeue=False) (dead-letter now)
          - callback raises anything else        -> retry via republish up to max_retries,
                                                    then nack(requeue=False) (dead-letter)
        """
        pool = self._require_pool()
        async with pool.acquire() as channel:
            await channel.set_qos(prefetch_count=self._prefetch_count)
            queue = await channel.get_queue(queue_name, ensure=True)
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    await self._process_message(queue_name, message, callback)

    async def _process_message(
        self,
        queue_name: str,
        message: AbstractIncomingMessage,
        callback: ConsumerCallback,
    ) -> None:
        retry_count = _header_int(message.headers, RETRY_COUNT_HEADER)
        bind_correlation_id(
            message.correlation_id or "unknown",
            message_id=message.message_id,
        )
        try:
            try:
                await callback(message)
            except MessagePoisonError:
                await message.nack(requeue=False)
                return
            except Exception:  # noqa: BLE001 - transient failure path: retry then dead-letter
                if retry_count >= self._max_retries:
                    await message.nack(requeue=False)
                else:
                    # Republish a fresh copy with an incremented retry header, then ack the
                    # original. nack(requeue=True) cannot carry an incremented counter.
                    await self._republish_for_retry(queue_name, message, retry_count + 1)
                    await message.ack()
                return
            await message.ack()
        finally:
            structlog.contextvars.clear_contextvars()

    async def _republish_for_retry(
        self,
        queue_name: str,
        message: AbstractIncomingMessage,
        next_retry_count: int,
    ) -> None:
        headers: dict[str, Any] = dict(message.headers or {})
        headers[RETRY_COUNT_HEADER] = next_retry_count
        retry_message = aio_pika.Message(
            body=message.body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type=message.content_type,
            message_id=message.message_id or str(uuid.uuid4()),
            correlation_id=message.correlation_id,
            headers=headers,
        )
        pool = self._require_pool()
        async with pool.acquire() as channel:
            # Re-enqueue directly onto the owned work queue via the default exchange, so the retry
            # does not fan out again through feed.events to sibling consumers.
            await channel.default_exchange.publish(retry_message, routing_key=queue_name)

    async def __aenter__(self) -> RabbitMQClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()


def _header_int(headers: Any, key: str) -> int:
    if not headers:
        return 0
    value = headers.get(key)
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0
