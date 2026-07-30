# E05 - Market Data Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

> POC-6 finding (resolved 2026-07-28): Yahoo series are provider reference closes only. P06/T03 froze the `poc6-yahoo-reference-v1` policy (Yahoo raw daily closes for `GC=F`/`BZ=F`, provider-managed continuous include-all rollover, no validated fallback), which unblocked E05. A 2026-07-28 spike re-confirmed Stooq is unusable (JavaScript anti-bot challenge on the CSV endpoint), so the service is Yahoo-chart only with `fallback=null`. See [T02](S01-Price-Data-Adapters/T02-stooq-fallback-adapter.md).

> **POC-7 provider migration (2026-07-30): Yahoo retired, replaced by biquote.io.** The live Yahoo chart endpoint rate-limits this host's IP (`HTTP 429`), which left every scored prediction unable to fetch a price (7 stuck `PENDING`). POC-7 ([backlog/POC/poc-7-biquote-price-source.md](../POC/poc-7-biquote-price-source.md)) validated **biquote.io** as a key-less, non-throttled replacement covering both assets, and the migration is implemented: the frozen policy is now **`biquote-reference-v1`** (`GOLD`→`XAUUSD`, `BRENT_OIL`→`UKOIL`, still `PROVIDER_DAILY_CLOSE` / include-all rollover / `fallback=null`), the `YahooChartAdapter` is replaced by `BiquoteAdapter` (`market_data/adapters/biquote.py`), and the 7 stale `poc6-yahoo` evaluations were purged. All Yahoo/`GC=F`/`BZ=F`/`yfinance` references below are **superseded** — retained for history, non-authoritative. The session calendar (America/New_York, 17:00 completion, baseline/settlement resolution) is unchanged; only the price provider and symbols changed. Verified live: biquote returned `200 OK` and both GOLD closes persisted with `source=biquote.io`, `registry_version=biquote-reference-v1`.

## Overview

The Market Data Service is responsible for fetching real OHLC (Open/High/Low/Close) price data for assets that the Prediction Service has generated predictions for. It sits at a critical junction in the pipeline: it both consumes `price-requests` messages published by the Verification Service and runs on a scheduled timer to keep prices current.

When a prediction window closes (e.g. next trading-day close), the Verification Service publishes a `PriceRequested` message. The Market Data Service picks this up, fetches the actual closing price from yfinance (primary) or Stooq (fallback), stores the result in Postgres, and publishes a `PriceObserved` message back to the `prices` queue so Verification can complete the scoring.

The service also performs a 90-day historical backfill on startup so Verification has data immediately, without waiting for live fetches.

## Stories

| Story | Description |
|---|---|
| [S01 - Price Data Adapters](S01-price-data-adapters/README.md) | yfinance and Stooq adapters that provide a uniform `get_ohlc(symbol, date)` interface with automatic fallback and retry logic |
| [S02 - Price Request Handler & Storage](S02-price-request-handler-storage/README.md) | RabbitMQ consumer for the `price-requests` queue, APScheduler-based fetch scheduling, Postgres persistence, and `PriceObserved` publishing |

## Architecture Context

**Service:** `src/services/market-data/`

**Queues consumed:**
- `price-requests` — `PriceRequested` messages from Verification Service

**Queues published:**
- `prices` — `PriceObserved` messages consumed by Verification Service

**Database:**
- Postgres — `prices` table (OHLC rows per asset/date, unique index on `(asset_symbol, date)`)

**External APIs:**
- yfinance (primary) — wraps Yahoo Finance; no API key required
- Stooq via pandas-datareader (fallback) — free data source; different symbol format

**Scheduler:**
- APScheduler — fires price-poll jobs when prediction windows close

## Pipeline Position

```
Verification Service
      |
      | PriceRequested → price-requests queue
      |
      ▼
Market Data Service  ←── APScheduler timer
      |
      | PriceObserved → prices queue
      |
      ▼
Verification Service (scoring)
```

## Overall Acceptance Criteria

1. `get_ohlc(symbol, date)` returns a valid OHLC dict for all supported asset classes: gold (`GC=F`), brent oil (`BZ=F`), USD index (`DX-Y.NYB`), US equities (`SPY`), Swedish equities (`ERIC-B.ST`).
2. When yfinance fails after 3 retries, the service falls back to Stooq automatically and logs which adapter was used.
3. A `PriceRequested` message consumed from `price-requests` results in a `PriceObserved` message published to `prices` with matching `request_id` and `prediction_id`.
4. All prices are persisted in the `prices` Postgres table with a unique constraint on `(asset_symbol, date)`.
5. On service startup, the last 90 days of daily closes are backfilled for the standard asset set without blocking the message consumer.
6. In-memory price cache prevents redundant API calls for the same asset/date within a 1-hour window.
7. Weekend and market-holiday requests return the last available trading-day close price.
8. All code passes `ruff` linting and `mypy` type checking.
9. Unit tests cover adapter retry logic, fallback switching, cache hits, and Postgres upsert behaviour.
