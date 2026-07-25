# T01: Price Request Consumer

## Context

The Market Data Service must respond to `PriceRequested` messages published by the Verification Service. Each message says: "I need the closing price for asset X when window Y closes." This task implements the **RabbitMQ consumer** that receives those messages, uses **APScheduler** to trigger the fetch at the right moment, calls the adapter layer (S01), and publishes the result as a `PriceObserved` message to the `prices` queue. It is the central orchestration module of the Market Data Service, at `services/market-data/consumer.py`.

## Background

**Queue flow:**
```
Verification Service
  → publishes PriceRequested to price-requests queue
  → Market Data Service consumes it
  → schedules fetch for window_close_at
  → fetches OHLC (yfinance primary / Stooq fallback)
  → stores in Postgres (via S02-T02)
  → publishes PriceObserved to prices queue
  → Verification Service consumes PriceObserved
```

**APScheduler usage:** The `window_close_at` timestamp in each `PriceRequested` message tells the service when the prediction window expires. We add a one-time `DateTrigger` job for each message. When the job fires, it performs the actual fetch. This allows the consumer to ack the message immediately without holding it open until the window closes (which could be hours).

**Adapter fallback pattern:**
```python
try:
    result = await yfinance_adapter.get_ohlc(symbol, date)
except AdapterUnavailableError:
    logger.warning("yfinance_fallback_activated", symbol=symbol, date=str(date))
    result = await stooq_adapter.get_ohlc(symbol, date)
```

## Inputs

**From `price-requests` queue — `PriceRequested` message schema** (defined in `src/shared/schemas/price_requests.py`):
```python
class PriceRequested(BaseModel):
    request_id: str          # UUID, for correlation
    prediction_id: str       # UUID, matches PredictionMade
    asset: str               # Yahoo Finance symbol, e.g. 'GC=F', 'ERIC-B.ST'
    window_close_at: datetime  # UTC datetime when the prediction window closes
```

**Environment variables:**
- `RABBITMQ_URL` — e.g. `amqp://guest:guest@localhost:5672/`
- `POSTGRES_DSN` — e.g. `postgresql+asyncpg://user:pass@localhost:5432/marketdata`
- `PRICE_FETCH_DELAY_SECONDS` — optional int, default `300` (5 min grace period after window close to allow prices to settle)

## Outputs

**To `prices` queue — `PriceObserved` message schema** (defined in `src/shared/schemas/prices.py`):
```python
class PriceObserved(BaseModel):
    request_id: str          # Echoed from PriceRequested for correlation
    prediction_id: str       # Echoed from PriceRequested
    asset: str               # Yahoo Finance symbol
    open: float
    high: float
    low: float
    close: float
    volume: int
    observed_at: datetime    # UTC datetime of the actual trading day returned
```

**Side effects:**
- Row inserted/upserted into `prices` Postgres table (via `price_store.save_price()`)
- Structured log entry at INFO level for every successful publish

## Technical Requirements

### File locations
- `services/market-data/consumer.py` — main consumer class
- `services/market-data/price_fetcher.py` — fetch orchestration (adapter selection + fallback)
- `services/market-data/main.py` — service entry point, starts consumer + scheduler + backfill
- `services/market-data/tests/test_consumer.py`

### Libraries
- `aio-pika>=9.0` — async RabbitMQ client
- `apscheduler>=3.10` — `AsyncIOScheduler` with `DateTrigger`
- `shared.rabbitmq.RabbitMQClient` — shared wrapper (handles connection, channel, ack/nack)
- `shared.schemas.price_requests.PriceRequested`
- `shared.schemas.prices.PriceObserved`

### Consumer class skeleton
```python
class PriceRequestConsumer:
    def __init__(
        self,
        rabbitmq_client: RabbitMQClient,
        scheduler: AsyncIOScheduler,
        price_fetcher: PriceFetcher,
        price_store: PriceStore,
    ) -> None: ...

    async def start(self) -> None:
        """Begin consuming from price-requests queue."""
        await self.rabbitmq_client.consume(
            queue="price-requests",
            callback=self._on_message,
        )

    async def _on_message(self, message: aio_pika.IncomingMessage) -> None:
        async with message.process():
            request = PriceRequested.model_validate_json(message.body)
            run_time = request.window_close_at + timedelta(
                seconds=int(os.getenv("PRICE_FETCH_DELAY_SECONDS", "300"))
            )
            self.scheduler.add_job(
                self._fetch_and_publish,
                trigger=DateTrigger(run_date=run_time),
                kwargs={"request": request},
                id=request.request_id,
                replace_existing=True,
            )
            logger.info("price_fetch_scheduled", request_id=request.request_id, run_at=str(run_time))

    async def _fetch_and_publish(self, request: PriceRequested) -> None:
        """Called by APScheduler when window closes."""
        ...
```

### `price_fetcher.py` orchestration
```python
class PriceFetcher:
    async def fetch(self, symbol: str, date: datetime.date) -> OHLCResult:
        try:
            return await yfinance_adapter.get_ohlc(symbol, date)
        except AdapterUnavailableError:
            logger.warning("yfinance_fallback_activated", symbol=symbol, date=str(date))
            return await stooq_adapter.get_ohlc(symbol, date)
```

### Message acking
- Ack immediately on receipt (`message.process()` context manager handles this)
- If scheduling fails, nack with `requeue=False` and log the error — do not requeue infinitely
- If `_fetch_and_publish` fails, log at ERROR level but do not republish the request (the Verification Service has a deadline mechanism)

### APScheduler configuration
```python
scheduler = AsyncIOScheduler(
    job_defaults={"misfire_grace_time": 3600},  # 1 hour grace for missed fires
    timezone="UTC",
)
```
`misfire_grace_time=3600` ensures jobs that missed their run time (e.g. service restart) still execute within 1 hour.

## Acceptance Criteria

1. Consuming a `PriceRequested` message results in an APScheduler job being registered with a `DateTrigger` set to `window_close_at + PRICE_FETCH_DELAY_SECONDS`.
2. The RabbitMQ message is acked immediately after scheduling, not after the fetch completes.
3. When the scheduled job fires, `price_fetcher.fetch(asset, window_close_date)` is called.
4. A successful fetch results in a `PriceObserved` message published to the `prices` queue with `request_id` and `prediction_id` matching the original `PriceRequested`.
5. A successful fetch results in `price_store.save_price()` being called with the OHLC data.
6. When yfinance raises `AdapterUnavailableError`, the consumer logs `yfinance_fallback_activated` at WARNING level and calls the Stooq adapter.
7. When both adapters raise `AdapterUnavailableError`, the consumer logs at ERROR level and does not publish a `PriceObserved` message (no partial/empty messages on the queue).
8. A duplicate `PriceRequested` with the same `request_id` replaces the existing APScheduler job (`replace_existing=True`) without raising an error.
9. `pytest services/market-data/tests/test_consumer.py -v` passes with all adapters and RabbitMQ mocked.
10. No mypy or ruff errors.

## Implementation Notes

- **Grace period rationale:** `PRICE_FETCH_DELAY_SECONDS=300` (5 minutes) gives Yahoo Finance time to publish the official closing price after market close. Without this, the fetch may return the last intraday price rather than the official close.
- **Date extraction from `window_close_at`:** Use `request.window_close_at.date()` to get the calendar date for `get_ohlc(symbol, date)`. The OHLC adapters work on calendar dates, not datetimes.
- **APScheduler + asyncio:** Use `AsyncIOScheduler`, not `BackgroundScheduler`. `AsyncIOScheduler` integrates with the running event loop and allows `async def` jobs.
- **Job ID collision:** Using `request_id` as the APScheduler job ID ensures idempotency. If the consumer restarts and replays a message, `replace_existing=True` prevents a duplicate job.
- **Persistent job store:** For production resilience, configure APScheduler with a `SQLAlchemyJobStore` backed by the same Postgres DB. For the MVP, the default in-memory store is acceptable but note that scheduled jobs are lost on restart.
- **`main.py` startup order:** (1) connect to RabbitMQ, (2) run DB migrations / ensure table exists, (3) start APScheduler, (4) run backfill (S02-T02), (5) start consumer. Ensure backfill does not block consumer startup — run it as a background task via `asyncio.create_task`.

## Definition of Done

- [ ] `services/market-data/consumer.py` implements `PriceRequestConsumer` with `start()` and `_on_message()` methods
- [ ] `services/market-data/price_fetcher.py` implements `PriceFetcher` with yfinance→Stooq fallback
- [ ] `services/market-data/main.py` starts scheduler, backfill, and consumer in the correct order
- [ ] RabbitMQ messages acked immediately on receipt, not after fetch
- [ ] APScheduler uses `DateTrigger` with `misfire_grace_time=3600`
- [ ] `PriceObserved` published to `prices` queue with all required fields
- [ ] `yfinance_fallback_activated` WARNING log emitted on fallback
- [ ] Both adapters failing results in ERROR log, no queue message published
- [ ] Unit tests mock adapters, RabbitMQ, and APScheduler; all tests pass
- [ ] `ruff check services/market-data/` passes
- [ ] `mypy services/market-data/` passes
