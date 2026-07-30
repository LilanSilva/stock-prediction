# T02: Price Storage & Historical Backfill

> **POC-7 provider note (2026-07-30):** the `source` values (`'yfinance'`/`'stooq'`) and yfinance bulk-download references below are superseded — observations are now stored with `source='biquote.io'`, `provider_symbol` `XAUUSD`/`UKOIL`, and `registry_version='biquote-reference-v1'`. The storage/outbox design in this task's As-built section is unchanged by the provider swap. See [POC-7](../../POC/poc-7-biquote-price-source.md).

## Context

The Market Data Service must persist every fetched OHLC price to Postgres so that the Verification Service can look up historical closes, and so that the system has price data available immediately on first startup. This task implements the **`prices` Postgres table schema**, the **upsert (insert-or-update) logic** via SQLAlchemy, the **90-day historical backfill** that runs at service startup, and the **1-hour in-memory price cache** that prevents redundant API calls for prices already fetched in the current hour. This module is `src/services/market-data/price_store.py`.

## Background

Without a backfill, the Verification Service would have no price data for predictions made before the Market Data Service was deployed, and no way to score historical predictions. By fetching 90 days of daily closes for the standard asset set on startup, we ensure Verification can immediately score any recent prediction.

The unique constraint on `(asset_symbol, date)` ensures idempotency: re-running the backfill or re-consuming a `PriceRequested` message never creates duplicate rows.

**Standard asset set for backfill:**
- `GC=F` — Gold
- `BZ=F` — Brent crude oil
- `OMXS30=F` — OMX Stockholm 30 index

Additional assets are fetched on demand (when a `PriceRequested` arrives for them).

**Cache design:** A simple `dict[tuple[str, date], OHLCResult]` keyed by `(symbol, date)` with a timestamp of insertion. On every `get_ohlc` call, check the cache first. Entries older than 3600 seconds are considered stale and evicted lazily.

## Inputs

- `OHLCResult` objects from the adapter layer (S01)
- `POSTGRES_DSN` environment variable
- `BACKFILL_DAYS` environment variable — optional int, default `90`
- `BACKFILL_ASSETS` environment variable — optional comma-separated list, default `GC=F,BZ=F,OMXS30=F`
- `PRICE_CACHE_TTL_SECONDS` environment variable — optional int, default `3600`

## Outputs

**Postgres table `prices`:**
```sql
CREATE TABLE IF NOT EXISTS prices (
    price_id      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_symbol  VARCHAR(32)  NOT NULL,
    date          DATE         NOT NULL,
    open          NUMERIC(18,6) NOT NULL,
    high          NUMERIC(18,6) NOT NULL,
    low           NUMERIC(18,6) NOT NULL,
    close         NUMERIC(18,6) NOT NULL,
    volume        BIGINT       NOT NULL DEFAULT 0,
    source        VARCHAR(16)  NOT NULL,   -- 'yfinance' or 'stooq'
    fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_prices_symbol_date UNIQUE (asset_symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_prices_symbol_date
    ON prices (asset_symbol, date DESC);
```

**`PriceStore` class methods:**
- `save_price(result: OHLCResult) -> None` — upserts a single OHLC row
- `get_price(symbol: str, date: date) -> OHLCResult | None` — reads from cache then DB
- `run_backfill() -> None` — async; fetches 90 days for standard assets

**Structured log entries:**
- `backfill_started` at INFO with `assets` and `days` fields
- `backfill_complete` at INFO with `assets`, `days`, `rows_inserted`, `rows_skipped` fields
- `cache_hit` at DEBUG with `symbol` and `date` fields

## Technical Requirements

### File locations
- `src/services/market-data/price_store.py`
- `src/services/market-data/migrations/001_create_prices_table.sql` (or Alembic migration)
- `src/services/market-data/tests/test_price_store.py`

### Libraries
- `sqlalchemy>=2.0` with async engine (`create_async_engine`)
- `asyncpg>=0.29` — async Postgres driver
- `shared.schemas.prices.OHLCResult` — input/output data model

### `PriceStore` class skeleton
```python
from datetime import date, datetime, timedelta
from typing import Optional
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

class PriceStore:
    _cache: dict[tuple[str, date], tuple[OHLCResult, datetime]]

    def __init__(self, dsn: str, cache_ttl_seconds: int = 3600) -> None:
        self._engine = create_async_engine(dsn, pool_size=5, max_overflow=10)
        self._cache = {}
        self._cache_ttl = cache_ttl_seconds

    async def save_price(self, result: OHLCResult) -> None:
        """Upsert OHLC row. On conflict (asset_symbol, date), update all fields."""
        ...

    async def get_price(self, symbol: str, target_date: date) -> Optional[OHLCResult]:
        """Check cache first, then DB. Returns None if not found."""
        ...

    async def run_backfill(
        self,
        assets: list[str],
        days: int = 90,
        fetcher: "PriceFetcher" = None,
    ) -> None:
        """Fetch last `days` of closes for each asset. Skip if already in DB."""
        ...

    def _is_cache_valid(self, inserted_at: datetime) -> bool:
        return (datetime.utcnow() - inserted_at).total_seconds() < self._cache_ttl
```

### Upsert SQL (via SQLAlchemy `pg_insert`)
```python
stmt = pg_insert(PricesTable).values(
    asset_symbol=result.symbol,
    date=result.date,
    open=result.open,
    high=result.high,
    low=result.low,
    close=result.close,
    volume=result.volume,
    source=result.source,
    fetched_at=datetime.utcnow(),
).on_conflict_do_update(
    constraint="uq_prices_symbol_date",
    set_=dict(
        open=result.open,
        high=result.high,
        low=result.low,
        close=result.close,
        volume=result.volume,
        source=result.source,
        fetched_at=datetime.utcnow(),
    ),
)
```

### Backfill algorithm
```python
async def run_backfill(self, assets, days, fetcher):
    logger.info("backfill_started", assets=assets, days=days)
    rows_inserted = rows_skipped = 0
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    for asset in assets:
        # Fetch the whole range at once with yfinance bulk download
        # rather than one-by-one to avoid N*90 API calls
        df = await fetcher.fetch_range(asset, start_date, end_date)
        for trading_date, row in df.iterrows():
            existing = await self.get_price(asset, trading_date)
            if existing:
                rows_skipped += 1
                continue
            await self.save_price(OHLCResult(
                symbol=asset,
                date=trading_date,
                **row,
                source="yfinance",
            ))
            rows_inserted += 1

    logger.info("backfill_complete",
                assets=assets, days=days,
                rows_inserted=rows_inserted,
                rows_skipped=rows_skipped)
```

Add `fetch_range(symbol, start, end) -> pd.DataFrame` to `PriceFetcher` in `price_fetcher.py`.

### Cache implementation
```python
async def get_price(self, symbol: str, target_date: date) -> Optional[OHLCResult]:
    key = (symbol, target_date)
    if key in self._cache:
        result, inserted_at = self._cache[key]
        if self._is_cache_valid(inserted_at):
            logger.debug("cache_hit", symbol=symbol, date=str(target_date))
            return result
        del self._cache[key]  # lazy eviction
    # Fall through to DB lookup
    row = await self._query_db(symbol, target_date)
    if row:
        self._cache[key] = (row, datetime.utcnow())
    return row
```

### DB query for `get_price`
```sql
SELECT * FROM prices
WHERE asset_symbol = :symbol AND date <= :target_date
ORDER BY date DESC
LIMIT 1;
```
This returns the most recent available row on or before `target_date`, handling weekends and holidays at the DB layer.

## Acceptance Criteria

1. `save_price(result)` inserts a row into the `prices` table; calling it again with the same `(symbol, date)` updates the existing row rather than raising a unique-constraint error.
2. `get_price('GC=F', <cached_date>)` returns the cached result without hitting the DB (verified by mocking the DB and confirming zero DB calls on second access within 1 hour).
3. `get_price` returns `None` when no row exists in DB or cache.
4. A cache entry older than `PRICE_CACHE_TTL_SECONDS` is evicted and the DB is queried on the next access.
5. `run_backfill` called with `assets=['GC=F', 'BZ=F', 'OMXS30=F'], days=90` results in up to 90 × 3 rows in `prices` (fewer due to weekends/holidays).
6. Running `run_backfill` twice does not insert duplicate rows (`rows_skipped` equals `rows_inserted` on the second run).
7. The `prices` table has the `UNIQUE` constraint on `(asset_symbol, date)` confirmed by the migration SQL.
8. `backfill_complete` log entry is emitted with correct `rows_inserted` and `rows_skipped` counts.
9. `get_price` with a weekend date returns the most recent preceding trading-day row (via the `date <= :target_date ORDER BY date DESC LIMIT 1` query).
10. All tests pass under `pytest -v` with Postgres replaced by an in-memory SQLite or `pytest-asyncio` + test Postgres container.

## Implementation Notes

- **`NUMERIC(18,6)`** for price columns avoids floating-point rounding errors in Postgres. Cast to `float` when populating `OHLCResult`.
- **Backfill concurrency:** Use `asyncio.gather` with a semaphore (`asyncio.Semaphore(3)`) to fetch multiple assets in parallel while respecting rate limits. Do not fire all 3 assets simultaneously without throttling.
- **Backfill does not block consumer:** Call `asyncio.create_task(price_store.run_backfill(...))` in `main.py` so the RabbitMQ consumer starts immediately. The backfill runs concurrently in the background.
- **Migration strategy:** For the MVP, the `prices` table can be created via a plain SQL file executed on startup (`CREATE TABLE IF NOT EXISTS ...`). Add Alembic if the team adopts it for other services.
- **Cache thread safety:** The cache dict is accessed only from async coroutines running on a single event loop — no locks needed.
- **`fetch_range` bulk download:** `yf.download(symbol, start=str(start), end=str(end))` returns a DataFrame with all trading days in the range. This is far more efficient than 90 individual `get_ohlc` calls. Implement this as a separate method on `PriceFetcher`.
- **Volume NaN:** Commodity futures and indices sometimes have `NaN` volume from yfinance. Coerce to `0` before storing: `int(row.get('Volume', 0) or 0)`.
- **`fetched_at` timezone:** Store all timestamps as UTC (`TIMESTAMPTZ`). Use `datetime.utcnow()` or `datetime.now(tz=timezone.utc)` — do not mix naive and tz-aware datetimes.

## Definition of Done

> The original checklist below predates the contract freeze (SQLAlchemy `prices` table, upsert-on-update,
> 90-day backfill, 1-hour cache). It is retained for history; superseded lines are marked and the
> authoritative outcome is the **As-built** checklist that follows.

- [~] `src/services/market-data/price_store.py` implements `PriceStore` with `save_price`, `get_price`, and `run_backfill` — *superseded: split into `market_data/db.py` (schema DDL) and `market_data/storage.py` (`PriceRequestRepository` + `OutboxPublisher`)*
- [~] `src/services/market-data/migrations/001_create_prices_table.sql` (or equivalent Alembic migration) creates the `prices` table with correct schema and unique constraint — *superseded: `db.py` applies the `market_data` schema idempotently at startup (per-service DDL ownership); tables are `price_requests`, `close_observations`, `outbox` — not a single `prices` table*
- [~] `save_price` uses PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` (upsert) — *superseded: observations are immutable — `ON CONFLICT (asset_id, session, registry_version) DO NOTHING`, never updated*
- [~] `get_price` checks in-memory cache before querying DB — *superseded: no cache; Verification requests exactly the sessions it needs*
- [~] Cache entries expire after `PRICE_CACHE_TTL_SECONDS` (default 3600) — *superseded: no cache*
- [~] `run_backfill` runs as a background task via `asyncio.create_task` in `main.py` — *superseded: no 90-day backfill; instead pending and publish-pending work is rehydrated on startup*
- [~] Backfill fetches 90 days for `GC=F`, `BZ=F`, `OMXS30=F` by default — *superseded: no backfill; OMXS30 is a deferred asset*
- [~] `backfill_started` and `backfill_complete` log entries emitted with correct fields — *superseded: no backfill; startup logs rehydration/outbox reconciliation instead*
- [x] Unit tests cover storage helpers (content hash, `build_price_observed`); live integration tests cover request idempotency, immutable dual-close persistence, and outbox delivery
- [x] `ruff check` passes on `market_data/db.py` and `market_data/storage.py`
- [x] `mypy --strict` passes on `market_data/db.py` and `market_data/storage.py`
- [x] `pytest` all tests green for the storage suite

### As-built (implemented 2026-07-28)

- [x] `market_data/db.py` applies the `market_data` schema idempotently: `price_requests`, `close_observations`, `outbox` (asyncpg, not SQLAlchemy)
- [x] `market_data/storage.py` `PriceRequestRepository` registers requests idempotently on `request_id` and persists immutable observations keyed on `(asset_id, session, registry_version)` with a content hash
- [x] `complete_request` stores both closes and enqueues exactly one `PriceObserved` outbox row atomically (idempotent via a unique `aggregate_id = request_id`)
- [x] `OutboxPublisher` relays pending `PriceObserved` rows to `feed.events` and reconciles rows left pending after a crash/restart
- [x] Both close observations record price kind, source, provider symbol, provider bar time, fetch time, adjustment flag, and registry version
