# T02: RabbitMQ Async Client Wrapper

## Context

This task builds the async RabbitMQ client wrapper in `src/shared/messaging/client.py`. Every one of the six microservices uses this wrapper to publish and consume messages. A well-built wrapper here prevents duplicating connection management, retry logic, and error handling in every service.

The wrapper uses `aio-pika`, an asyncio-native Python library for AMQP 0-9-1 (RabbitMQ's protocol). It must handle transient network failures gracefully — services should reconnect automatically without crashing.

## Background

### aio-pika overview

`aio-pika` provides:
- `connect_robust()` — creates a connection with automatic reconnection on disconnect
- `RobustConnection` / `RobustChannel` — transparent reconnection
- `Message` — AMQP message with delivery_mode, content_type, headers
- `IncomingMessage` — received message with ack()/nack() methods
- `Queue.consume()` — async generator-style consumer

Use `aio_pika.connect_robust()` rather than `aio_pika.connect()` — the robust variant reconnects automatically and re-declares consumers on reconnect.

### Connection pooling

For services that publish frequently (e.g., Ingestion publishing one message per article), a single persistent connection with a channel pool is sufficient. Use `aio_pika.pool.Pool` for channel pooling — channels are lightweight (one per concurrent operation) and should not be shared across coroutines.

### Dead-letter routing on nack

When a consumer calls `message.nack(requeue=False)`, RabbitMQ routes the message to the dead-letter exchange (`feed.dlx`) configured on the queue (declared in T04). The wrapper must never requeue failed messages after the max retry attempts — dead-lettering is the failure path.

### Retry strategy

Transient failures (e.g., LLM timeout, database connection error) should cause the consumer to nack with requeue=True for up to `max_retries` attempts. After `max_retries` exhaustion, nack with requeue=False to dead-letter. Track retry count in the AMQP message headers: `x-retry-count`.

## Inputs

- `RABBITMQ_URL` environment variable: `amqp://feed_user:password@localhost:5672/`
- Message schemas from T01: `FeedMessage` base class and its subclasses
- Queue names: `raw-news`, `events`, `predictions`, `price-requests`, `prices`, `scored-predictions`

## Outputs

- `src/shared/messaging/client.py` — `RabbitMQClient` class
- `src/shared/messaging/exceptions.py` — custom exception classes
- `src/shared/messaging/__init__.py` — re-exports
- `tests/test_messaging.py` — pytest-asyncio tests

## Technical Requirements

### Dependencies (add to `src/shared/pyproject.toml`)

```toml
dependencies = [
    "pydantic>=2.7,<3",
    "pydantic-settings>=2.3,<3",
    "aio-pika>=9.4,<10",
]
```

### `src/shared/messaging/exceptions.py`

```python
class RabbitMQConnectionError(Exception):
    """Raised when connection to RabbitMQ cannot be established."""

class MessagePublishError(Exception):
    """Raised when a message fails to publish after retries."""

class MessageProcessingError(Exception):
    """Raised by consumer callback to signal a retriable processing failure."""

class MessagePoisonError(Exception):
    """Raised by consumer callback to signal a non-retriable failure (dead-letter immediately)."""
```

### `src/shared/messaging/client.py` — `RabbitMQClient` class

```python
class RabbitMQClient:
    def __init__(
        self,
        url: str,                          # AMQP URL
        max_retries: int = 3,              # max consumer retries before dead-letter
        prefetch_count: int = 10,          # QoS prefetch per consumer
    ) -> None: ...

    async def connect(self) -> None:
        """Establish robust connection and initialize channel pool."""

    async def close(self) -> None:
        """Close all channels and the connection gracefully."""

    async def publish(
        self,
        queue_name: str,
        message: FeedMessage,
        priority: int = 0,
    ) -> None:
        """Publish a FeedMessage to the named queue.
        
        Sets delivery_mode=PERSISTENT (2), content_type='application/json',
        and includes correlation_id in AMQP message properties.
        Raises MessagePublishError on failure.
        """

    async def consume(
        self,
        queue_name: str,
        callback: Callable[[IncomingMessage], Awaitable[None]],
    ) -> None:
        """Start consuming from queue_name.
        
        Calls callback for each message. Handles ack/nack based on exceptions:
        - No exception: ack
        - MessageProcessingError: nack(requeue=True) up to max_retries, then dead-letter
        - MessagePoisonError: nack(requeue=False) immediately
        - Any other exception: nack(requeue=True) up to max_retries
        """

    async def __aenter__(self) -> 'RabbitMQClient': ...
    async def __aexit__(self, *args: Any) -> None: ...
```

### Publish implementation details

```python
async def publish(self, queue_name: str, message: FeedMessage, priority: int = 0) -> None:
    async with self._channel_pool.acquire() as channel:
        amqp_message = aio_pika.Message(
            body=message.to_amqp_body(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            correlation_id=message.correlation_id,
            message_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            headers={"schema_version": message.schema_version},
        )
        await channel.default_exchange.publish(
            amqp_message,
            routing_key=queue_name,
        )
```

### Consume implementation details

```python
async def consume(self, queue_name: str, callback: Callable[..., Awaitable[None]]) -> None:
    async with self._channel_pool.acquire() as channel:
        await channel.set_qos(prefetch_count=self._prefetch_count)
        queue = await channel.get_queue(queue_name)
        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                await self._process_message(message, callback)

async def _process_message(
    self,
    message: IncomingMessage,
    callback: Callable[..., Awaitable[None]],
) -> None:
    retry_count = int(message.headers.get("x-retry-count", 0))
    try:
        async with message.process(ignore_processed=True):
            await callback(message)
            await message.ack()
    except MessagePoisonError:
        await message.nack(requeue=False)
    except (MessageProcessingError, Exception):
        if retry_count >= self._max_retries:
            await message.nack(requeue=False)  # → dead-letter
        else:
            # Republish with incremented retry count
            # (requeue=True resets retry headers — republish instead)
            await message.nack(requeue=False)
            await self._republish_with_retry(message, retry_count + 1)
```

### Channel pool setup

```python
from aio_pika.pool import Pool

self._connection: aio_pika.RobustConnection = await aio_pika.connect_robust(url)

async def _get_channel() -> aio_pika.RobustChannel:
    return await self._connection.channel()

self._channel_pool: Pool[aio_pika.RobustChannel] = Pool(
    _get_channel, max_size=10
)
```

### Settings model (for service use)

Add to `src/shared/messaging/client.py` or a separate `src/shared/config.py`:

```python
from pydantic_settings import BaseSettings

class RabbitMQSettings(BaseSettings):
    rabbitmq_url: str = "amqp://feed_user:changeme@localhost:5672/"
    rabbitmq_max_retries: int = 3
    rabbitmq_prefetch_count: int = 10

    model_config = ConfigDict(env_prefix="")
```

## Acceptance Criteria

1. `from shared.messaging.client import RabbitMQClient` imports without error.
2. `RabbitMQClient` connects to RabbitMQ running in Docker (integration test, requires S01).
3. A message published to `raw-news` is received by a consumer on `raw-news` with the correct body.
4. The received message deserializes to the correct Pydantic model via `ArticleIngested.from_amqp_body()`.
5. When the consumer callback raises `MessagePoisonError`, the message is dead-lettered (appears in `raw-news.dlq`).
6. When the consumer callback raises `MessageProcessingError` three times (equal to `max_retries=3`), the message is dead-lettered after the third failure.
7. When the consumer callback succeeds, the message is acknowledged and removed from the queue.
8. Published messages have `delivery_mode=PERSISTENT` (verify via RabbitMQ management UI message properties).
9. `correlation_id` from the `FeedMessage` is set as the AMQP message correlation ID property.
10. The client can be used as an async context manager (`async with RabbitMQClient(url) as client:`).
11. `pytest tests/test_messaging.py -v` passes (unit tests with mocked aio-pika, plus integration tests marked `@pytest.mark.integration`).

## Implementation Notes

- Use `aio_pika.connect_robust()` not `aio_pika.connect()`. The robust variant automatically reconnects when the TCP connection drops, which happens during Docker container restarts in development.
- The `max_size=10` for the channel pool is conservative. Each microservice typically needs only 1-2 concurrent channels. Increase if throughput testing shows bottlenecks.
- Do not call `channel.declare_queue()` in the wrapper — queues are pre-declared by RabbitMQ's definitions.json (T04). Use `channel.get_queue()` to get a reference to an existing queue.
- For republishing with retry count: when requeuing after a transient failure, do not use `nack(requeue=True)` — this puts the message back at the head of the queue and does not allow incrementing the retry header. Instead, nack (dead-letter if truly failed), or publish a new message to the same queue with the incremented `x-retry-count` header.
- Unit tests should mock `aio_pika` using `pytest-mock` or `unittest.mock.AsyncMock`. Mark integration tests with `@pytest.mark.integration` and skip them if `RABBITMQ_URL` is not set.
- The `prefetch_count=10` setting means RabbitMQ delivers at most 10 unacknowledged messages to a consumer. This prevents a slow consumer from accumulating an unbounded number of messages in memory.
- Type hint the callback as `Callable[[aio_pika.IncomingMessage], Awaitable[None]]` for mypy compatibility.

## Definition of Done

- [x] `src/shared/messaging/client.py` implements `RabbitMQClient` with `connect`, `close`, `publish`, `consume`, and context manager support
- [x] `src/shared/messaging/exceptions.py` defines all four exception classes
- [x] `src/shared/pyproject.toml` includes `aio-pika>=9.4,<10`
- [x] Unit tests (mocked) pass for publish, consume, ack, nack, dead-letter, retry logic
- [x] Integration test passes against live RabbitMQ (marked `@pytest.mark.integration`)
- [ ] Published messages are persistent (delivery_mode=2)
- [x] `mypy src/shared/messaging/ --strict` returns 0 errors
- [x] `ruff check src/shared/messaging/` returns 0 violations
