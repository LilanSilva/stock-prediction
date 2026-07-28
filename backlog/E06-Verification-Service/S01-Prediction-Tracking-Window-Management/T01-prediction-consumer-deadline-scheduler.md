# T01: Prediction consumer & deadline scheduler

> **POC-6 replacement requirements (2026-07-13):** Evaluation creation is blocked until P06/T03 approves the registry version. Resolve sessions under that frozen policy and require `PriceObserved` to match price kind, session, provider bar time, fetch time, adjustment flag, registry version, and rollover eligibility. Provider daily closes are not official settlements. Conflicting legacy queue/schema/session details below remain non-authoritative.

## Context

The Verification Service sits between the Prediction Service and the Credibility Service in the pipeline. This task builds the first half of that service: consuming `PredictionMade` messages from the `predictions` RabbitMQ queue, persisting each prediction to a Postgres `outcomes` table with `status = PENDING`, and scheduling a per-prediction APScheduler job that fires when the prediction's time window closes. On job fire, if price data is already available the job hands off to scoring; otherwise it republishes a `PriceRequested` message so the Market Data Service fetches the required close price.

## Background

**Prediction time horizon:** Each `PredictionMade` message carries a `time_horizon` field (e.g. `"1d"`) and a `created_at` timestamp. For a 1-trading-day horizon, `window_close_at` = next market close after `created_at`. For simplicity in V1, treat `1d` as `created_at + 24 hours` (calendar time). Future tasks can refine this to true trading-day logic using `pandas_market_calendars`.

**APScheduler:** Use `AsyncIOScheduler` from `apscheduler` with a `DateTrigger` set to `window_close_at`. Each job is identified by `job_id = f"verify_{prediction_id}"`. Store the scheduler as a module-level singleton. On service restart, pending jobs must be re-hydrated from the `outcomes` table (query all rows with `status = PENDING` and `window_close_at > now()`).

**Price lookup:** When the window-close job fires, query the `price_observations` table (written by the `prices` queue consumer, also in this service) for a row matching `prediction_id`. If found, call the scoring function directly. If not found, publish a `PriceRequested` message to the `price-requests` queue and update `outcomes.status = AWAITING_PRICE`.

**Idempotency:** The `predictions` queue consumer must be idempotent. If a `prediction_id` already exists in `outcomes`, skip insertion and log a warning (do not raise).

## Inputs

- **Queue:** `predictions` (RabbitMQ)
- **Message schema:** `shared.schemas.PredictionMade`
  ```
  prediction_id: str
  event_ids: list[str]
  asset: str
  direction: Literal["UP", "DOWN", "NEUTRAL"]
  magnitude_bucket: str
  confidence: float
  time_horizon: str          # e.g. "1d"
  rationale: str
  contributing_edges: list[dict]
  correlation_id: str
  created_at: datetime
  ```
- **Database:** Postgres `outcomes` table (see Technical Requirements for DDL)
- **Database:** Postgres `price_observations` table (read-only in this task; written by prices queue consumer)
- **Config (env vars):**
  - `POSTGRES_DSN` — e.g. `postgresql+asyncpg://user:pass@postgres:5432/feedanalyzer`
  - `RABBITMQ_URL` — e.g. `amqp://guest:guest@rabbitmq:5672/`
  - `PREDICTIONS_QUEUE` — default `predictions`
  - `PRICE_REQUESTS_QUEUE` — default `price-requests`

## Outputs

- **Database write:** New row in `outcomes` table with `status = PENDING`
- **APScheduler job:** One `DateTrigger` job per prediction, scheduled at `window_close_at`
- **Queue publish (conditional):** `PriceRequested` message to `price-requests` queue if price not yet available at window close
- **Database update (conditional):** `outcomes.status = AWAITING_PRICE` if price not found at window close

## Technical Requirements

### 1. Project layout

All code lives under `src/services/verification/`. Create the following files if they do not exist:

```
src/services/verification/
  app/
    __init__.py
    main.py              # FastAPI app + lifespan startup/shutdown
    consumers/
      __init__.py
      predictions.py     # THIS FILE — PredictionMade consumer
      prices.py          # Stub only in this task; full impl in S02
    scheduler.py         # APScheduler singleton + job registration
    db/
      __init__.py
      models.py          # SQLAlchemy ORM models
      session.py         # async sessionmaker
    schemas/
      __init__.py        # Re-export shared schemas if needed
  tests/
    __init__.py
    test_prediction_consumer.py
    test_scheduler.py
  Dockerfile
  requirements.txt
```

### 2. Postgres DDL — `outcomes` table

Create via Alembic migration or plain SQL init script at `infra/postgres/init/03_verification.sql`:

```sql
CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id       TEXT PRIMARY KEY,
    asset               TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,        -- UP | DOWN | NEUTRAL
    predicted_magnitude TEXT NOT NULL,
    confidence          FLOAT NOT NULL,
    time_horizon        TEXT NOT NULL,
    contributing_edges  JSONB NOT NULL DEFAULT '[]',
    sources             JSONB NOT NULL DEFAULT '[]',
    correlation_id      TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    window_close_at     TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | AWAITING_PRICE | SCORED
    actual_direction    TEXT,                -- populated after scoring
    actual_return       FLOAT,              -- populated after scoring
    is_correct          BOOLEAN,            -- populated after scoring
    score               FLOAT,              -- populated after scoring
    scored_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outcomes_status ON outcomes (status);
CREATE INDEX IF NOT EXISTS idx_outcomes_window_close_at ON outcomes (window_close_at);
```

Also create the `price_observations` table (used by the prices consumer, read here):

```sql
CREATE TABLE IF NOT EXISTS price_observations (
    request_id      TEXT PRIMARY KEY,
    prediction_id   TEXT NOT NULL,
    asset           TEXT NOT NULL,
    open            FLOAT,
    high            FLOAT,
    low             FLOAT,
    close           FLOAT NOT NULL,
    volume          FLOAT,
    observed_at     TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_obs_prediction_id ON price_observations (prediction_id);
```

### 3. SQLAlchemy ORM models (`app/db/models.py`)

Use `sqlalchemy.ext.asyncio` with `AsyncSession`. Define `Outcome` and `PriceObservation` mapped classes corresponding to the DDL above. Use `sqlalchemy.dialects.postgresql.JSONB` for the JSONB columns.

### 4. Predictions consumer (`app/consumers/predictions.py`)

```python
# Key logic (pseudocode — implement fully)
async def handle_prediction_made(message: aio_pika.IncomingMessage) -> None:
    async with message.process():
        payload = PredictionMade.model_validate_json(message.body)
        
        async with get_session() as session:
            # Idempotency check
            existing = await session.get(Outcome, payload.prediction_id)
            if existing:
                logger.warning("Duplicate prediction_id=%s, skipping", payload.prediction_id)
                return
            
            window_close_at = compute_window_close(payload.created_at, payload.time_horizon)
            
            outcome = Outcome(
                prediction_id=payload.prediction_id,
                asset=payload.asset,
                predicted_direction=payload.direction,
                predicted_magnitude=payload.magnitude_bucket,
                confidence=payload.confidence,
                time_horizon=payload.time_horizon,
                contributing_edges=payload.contributing_edges,
                sources=[],  # populated after scoring from event sources
                correlation_id=payload.correlation_id,
                created_at=payload.created_at,
                window_close_at=window_close_at,
                status="PENDING",
            )
            session.add(outcome)
            await session.commit()
        
        schedule_window_check(payload.prediction_id, window_close_at)
```

### 5. Window close computation (`app/scheduler.py`)

```python
def compute_window_close(created_at: datetime, time_horizon: str) -> datetime:
    """V1: simple calendar-time offset. Extend with pandas_market_calendars in future."""
    if time_horizon == "1d":
        return created_at + timedelta(hours=24)
    if time_horizon == "4h":
        return created_at + timedelta(hours=4)
    if time_horizon == "1w":
        return created_at + timedelta(days=7)
    # fallback
    return created_at + timedelta(hours=24)
```

### 6. Scheduler setup and job hydration (`app/scheduler.py`)

- Instantiate `AsyncIOScheduler` once at module level.
- `schedule_window_check(prediction_id, window_close_at)` adds a `DateTrigger` job with `job_id = f"verify_{prediction_id}"`. Use `replace_existing=True` to be safe.
- On service startup (FastAPI `lifespan`), call `hydrate_pending_jobs()` which queries `outcomes WHERE status IN ('PENDING', 'AWAITING_PRICE') AND window_close_at > NOW()` and re-schedules each.
- The job function `on_window_close(prediction_id)` must be async, accept the prediction_id, and:
  1. Query `price_observations` for any row with `prediction_id = prediction_id`.
  2. If found: import and call `score_prediction(prediction_id, price_row)` (stub in this task — full impl in S02/T01).
  3. If not found: publish `PriceRequested` to `price-requests` queue, update `outcomes.status = AWAITING_PRICE`.

### 7. PriceRequested message

Use schema `shared.schemas.PriceRequested`:
```
request_id: str      # generate with uuid4()
prediction_id: str
asset: str
window_close_at: datetime
```

Publish as JSON bytes to the `price-requests` queue using `shared.rabbitmq.RabbitMQClient`.

### 8. Prices consumer stub (`app/consumers/prices.py`)

In this task, implement only the Postgres write. Full scoring integration is in S02.

```python
async def handle_price_observed(message: aio_pika.IncomingMessage) -> None:
    async with message.process():
        payload = PriceObserved.model_validate_json(message.body)
        async with get_session() as session:
            obs = PriceObservation(
                request_id=payload.request_id,
                prediction_id=payload.prediction_id,
                asset=payload.asset,
                open=payload.open,
                high=payload.high,
                low=payload.low,
                close=payload.close,
                volume=payload.volume,
                observed_at=payload.observed_at,
            )
            session.merge(obs)  # upsert by request_id
            await session.commit()
```

### 9. Libraries

Add to `requirements.txt`:
```
fastapi>=0.111
uvicorn[standard]>=0.29
aio-pika>=9.4
sqlalchemy[asyncio]>=2.0
asyncpg>=0.29
apscheduler>=3.10
pydantic>=2.7
python-dotenv>=1.0
prometheus-client>=0.20
```

## Acceptance Criteria

1. When a valid `PredictionMade` JSON message is published to the `predictions` queue, a corresponding row appears in the `outcomes` table within 2 seconds with `status = PENDING` and a correct `window_close_at`.
2. A duplicate `PredictionMade` message (same `prediction_id`) is silently skipped — no error, no duplicate row, a warning is logged.
3. After the service inserts a row, an APScheduler job with `job_id = f"verify_{prediction_id}"` exists in the scheduler with the correct fire time.
4. On service restart, all `PENDING` and `AWAITING_PRICE` outcomes with `window_close_at > now()` are re-scheduled without duplicates.
5. When the scheduler job fires and no `PriceObservation` exists for the `prediction_id`, a `PriceRequested` message appears in the `price-requests` queue with correct `prediction_id` and `asset` fields.
6. When the scheduler job fires and a `PriceObservation` exists, no `PriceRequested` message is published.
7. `PriceObserved` messages consumed from the `prices` queue are persisted to `price_observations` table (upsert by `request_id`).
8. All database operations use `AsyncSession`; no synchronous SQLAlchemy calls.
9. Unit tests in `tests/test_prediction_consumer.py` and `tests/test_scheduler.py` pass under `pytest`.
10. `ruff check src/services/verification/` and `mypy src/services/verification/` report zero errors.

## Implementation Notes

- **APScheduler + asyncio:** Use `AsyncIOScheduler` (not `BackgroundScheduler`). The event loop must already be running when the scheduler starts. Start the scheduler inside the FastAPI `lifespan` async context manager, not at module import time.
- **Job persistence:** APScheduler V3 in-memory jobstore loses jobs on restart; the hydration query in step 6 compensates for this. Do not use SQLAlchemy jobstore to keep the dependency surface small.
- **Window close in the past:** If `window_close_at` is already in the past when a `PredictionMade` is consumed (e.g. replay of old messages), call `on_window_close` immediately rather than scheduling it.
- **Time zones:** Store and compare all datetimes as UTC (`datetime.timezone.utc`). Use `datetime.now(tz=timezone.utc)` never `datetime.utcnow()` (deprecated in Python 3.12).
- **aio-pika ack:** Always use `async with message.process():` so the message is acked on success and nacked on unhandled exception. Do not manually call `message.ack()`.
- **sources field:** `PredictionMade` does not carry a `sources` list directly. The sources come from the originating events, which are not available here. Leave `outcomes.sources = []` for now; S02/T02 can backfill from the events table if needed.
- **Testing without RabbitMQ:** Mock `shared.rabbitmq.RabbitMQClient.publish` with `unittest.mock.AsyncMock`. Use an in-memory SQLite DB via `aiosqlite` for unit tests to avoid Postgres dependency.

## Definition of Done

- [ ] Unit tests pass (`pytest src/services/verification/tests/`)
- [ ] `ruff check src/services/verification/` exits 0
- [ ] `mypy src/services/verification/` exits 0
- [ ] `outcomes` table DDL migration file exists at `infra/postgres/init/03_verification.sql`
- [ ] `price_observations` table DDL migration exists in the same file
- [ ] `src/services/verification/requirements.txt` is complete and pinned to minor versions
- [ ] Service starts cleanly in Docker Compose (`docker-compose up verification`)
- [ ] A manually published `PredictionMade` message results in a visible `PENDING` row in Postgres
- [ ] A scheduler job fires after the configured window and publishes `PriceRequested` when no price exists
- [ ] No synchronous blocking calls inside async functions
