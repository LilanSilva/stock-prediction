# S02 - Price Request Handler & Storage

## Overview

This story wires the Market Data Service into the RabbitMQ pipeline and provides persistence. It builds on the adapters from S01 to:

1. Consume `PriceRequested` messages from the `price-requests` queue
2. Schedule a price fetch for each prediction window's close time using APScheduler
3. Fetch the OHLC close price (yfinance → Stooq fallback)
4. Persist prices to Postgres with an upsert so duplicate fetches are idempotent
5. Publish `PriceObserved` messages to the `prices` queue for the Verification Service
6. Backfill 90 days of historical closes for standard assets on startup
7. Cache prices in memory for 1 hour to avoid redundant API calls

## Tasks

| Task | Description |
|---|---|
| [T01 - Price request consumer](T01-price-request-consumer.md) | RabbitMQ consumer, APScheduler job scheduling, fetch orchestration, and `PriceObserved` publishing |
| [T02 - Price storage & historical backfill](T02-price-storage-historical-backfill.md) | Postgres schema, upsert logic, 90-day startup backfill, and 1-hour in-memory cache |

## Dependencies

- **S01 must be complete** — `yfinance_adapter.get_ohlc` and `stooq_adapter.get_ohlc` must exist and pass tests
- `src/shared/` package: `RabbitMQClient`, `PriceRequested` schema, `PriceObserved` schema
- Postgres instance running (local: `infra/docker-compose.yml`)
- RabbitMQ instance running with `price-requests` and `prices` queues declared
- Environment variables: `RABBITMQ_URL`, `POSTGRES_DSN`

## How to Test End-to-End

1. Start infrastructure: `docker compose -f infra/docker-compose.yml up -d postgres rabbitmq`
2. Run DB migrations: `alembic upgrade head` from `src/services/market-data/`
3. Start the service: `docker compose up market-data`
4. Observe startup backfill log: `backfill_complete assets=3 days=90`
5. Publish a test `PriceRequested` message to the `price-requests` queue:
   ```json
   {
     "request_id": "test-001",
     "prediction_id": "pred-001",
     "asset": "GC=F",
     "window_close_at": "<now + 5 seconds>"
   }
   ```
6. After 5 seconds, verify:
   - A row exists in the `prices` table for `asset_symbol='GC=F'`
   - A `PriceObserved` message appears in the `prices` queue with `request_id='test-001'`
7. Publish the same request again; verify no duplicate row in Postgres and no error log (idempotent upsert).
8. Run unit tests: `pytest src/services/market-data/tests/ -v`
