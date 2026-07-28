# T02: PredictionScored publisher

## Context

This is the final task in the Verification Service pipeline. After scoring completes (S02/T01), this task builds the `PredictionScored` message, publishes it to the `scored-predictions` RabbitMQ queue for the Credibility Service, updates the `outcomes` table status to `SCORED`, and emits a rolling accuracy metric. This closes the Verification Service's responsibility in the pipeline.

## Background

**PredictionScored schema:** The `scored-predictions` queue is consumed by the Credibility Service, which uses `contributing_edges` and `sources` to perform proportional credit assignment. These fields must be accurate and non-empty for the Credibility Service to function correctly.

**Sources backfill:** The `PredictionMade` message does not carry a `sources` field directly. Sources originate from the `EventDetected` messages that fed the prediction. In V1, if the `outcomes.sources` column is empty (`[]`), leave the `sources` field in the published message as an empty list. Future work can join via the `events` table.

**Metric:** The `prediction_accuracy_rate` metric is a rolling 24-hour window: `correct_count / total_scored_count` for all predictions with `scored_at >= now() - 24h`. Emit via Prometheus counter/gauge using `prometheus_client`, or as a structured log entry if Prometheus is not yet wired up — but the code must support both modes via a config flag `EMIT_PROMETHEUS_METRICS=true/false`.

**Queue declaration:** The `scored-predictions` queue must be declared as durable to survive RabbitMQ restarts.

## Inputs

- **Function argument:** `ScoringResult` dataclass instance (from `app/scoring.py`, produced in S02/T01)
- **Database read:** `outcomes` table row (already updated by S02/T01 — read `contributing_edges`, `sources`, `predicted_magnitude` to fill message fields)
- **Config (env vars):**
  - `SCORED_PREDICTIONS_QUEUE` — default `scored-predictions`
  - `EMIT_PROMETHEUS_METRICS` — default `true`
  - `ACCURACY_METRIC_WINDOW_HOURS` — default `24`

## Outputs

- **Queue publish:** `PredictionScored` message to `scored-predictions` queue
- **Database update:** `outcomes.status = "SCORED"` (already done in S02/T01, but confirm idempotently — do not set to SCORED twice if already set)
- **Metric emission:** `prediction_accuracy_rate` gauge updated

## Technical Requirements

### 1. Module location

Create `src/services/verification/app/publisher.py`.

### 2. PredictionScored schema

Use `shared.schemas.PredictionScored`:
```python
class PredictionScored(BaseModel):
    prediction_id: str
    asset: str
    predicted_direction: str        # UP | DOWN | NEUTRAL
    actual_direction: str           # UP | DOWN | NEUTRAL
    predicted_magnitude: str        # small | medium | large
    actual_magnitude: str           # small | medium | large
    actual_return: float
    is_correct: bool
    score: float
    contributing_edges: list[dict]
    sources: list[str]
    scored_at: datetime
```

If this schema does not exist in `src/shared/schemas/`, create it there. Do not define it locally in the verification service.

### 3. Publisher function signature

```python
async def publish_prediction_scored(
    result: ScoringResult,
    session: AsyncSession,
    rabbitmq_client: RabbitMQClient,
) -> None:
    """
    Build PredictionScored from ScoringResult, publish to scored-predictions queue,
    update outcomes status, emit accuracy metric.
    """
```

### 4. Build the message

```python
message = PredictionScored(
    prediction_id=result.prediction_id,
    asset=result.asset,
    predicted_direction=result.predicted_direction,
    actual_direction=result.actual_direction,
    predicted_magnitude=result.predicted_magnitude,
    actual_magnitude=result.actual_magnitude,
    actual_return=result.actual_return,
    is_correct=result.is_correct,
    score=result.score,
    contributing_edges=result.contributing_edges,
    sources=result.sources,
    scored_at=result.scored_at,
)
```

### 5. Publish via RabbitMQ client

```python
await rabbitmq_client.publish(
    queue=settings.SCORED_PREDICTIONS_QUEUE,
    body=message.model_dump_json().encode(),
    durable=True,
)
```

The `RabbitMQClient.publish` method is from `shared.rabbitmq`. It must declare the queue as durable before publishing. If the shared client does not support a `durable` parameter, declare the queue separately:
```python
await rabbitmq_client.declare_queue(settings.SCORED_PREDICTIONS_QUEUE, durable=True)
```

### 6. Outcomes status update

After successful publish, confirm `outcomes.status = "SCORED"` (idempotent SET):
```python
await session.execute(
    update(Outcome)
    .where(Outcome.prediction_id == result.prediction_id)
    .values(status="SCORED")
)
await session.commit()
```

### 7. Accuracy metric

Create `src/services/verification/app/metrics.py`:

```python
from prometheus_client import Gauge
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta, timezone
from app.db.models import Outcome
from app.config import settings

ACCURACY_GAUGE = Gauge(
    "prediction_accuracy_rate",
    "Rolling 24h prediction accuracy rate",
)

async def update_accuracy_metric(session: AsyncSession) -> float:
    window_start = datetime.now(tz=timezone.utc) - timedelta(
        hours=settings.ACCURACY_METRIC_WINDOW_HOURS
    )
    result = await session.execute(
        select(
            func.count().label("total"),
            func.sum(
                func.cast(Outcome.is_correct, Integer)
            ).label("correct")
        )
        .where(Outcome.status == "SCORED")
        .where(Outcome.scored_at >= window_start)
    )
    row = result.one()
    total = row.total or 0
    correct = row.correct or 0
    rate = correct / total if total > 0 else 0.0
    
    if settings.EMIT_PROMETHEUS_METRICS:
        ACCURACY_GAUGE.set(rate)
    
    logger.info(
        "prediction_accuracy_rate",
        extra={"rate": rate, "total": total, "correct": correct,
               "window_hours": settings.ACCURACY_METRIC_WINDOW_HOURS}
    )
    return rate
```

Call `update_accuracy_metric(session)` inside `publish_prediction_scored` after committing the status update.

### 8. Prometheus HTTP endpoint

In `app/main.py`, mount the Prometheus metrics endpoint:
```python
from prometheus_client import make_asgi_app

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

This exposes `GET /metrics` for Prometheus scraping.

### 9. Scheduler integration

Update `app/scheduler.py` `on_window_close` to call the publisher:
```python
from app.publisher import publish_prediction_scored

async def on_window_close(prediction_id: str) -> None:
    async with get_session() as session:
        price_obs = await get_price_observation(prediction_id, session)
        if price_obs is None:
            await publish_price_request(prediction_id, session)
            return
        result = await score_prediction(prediction_id, session)
        if result:
            await publish_prediction_scored(result, session, get_rabbitmq_client())
```

`get_rabbitmq_client()` returns the module-level singleton `RabbitMQClient` instance initialized in `main.py` lifespan.

### 10. Shared schema update

Ensure `src/shared/schemas/__init__.py` exports `PredictionScored`. If the class does not yet exist in `src/shared/schemas/messages.py`, add it:

```python
class PredictionScored(BaseModel):
    prediction_id: str
    asset: str
    predicted_direction: str
    actual_direction: str
    predicted_magnitude: str
    actual_magnitude: str
    actual_return: float
    is_correct: bool
    score: float
    contributing_edges: list[dict]
    sources: list[str]
    scored_at: datetime
```

### 11. Config class

Create `src/services/verification/app/config.py` using `pydantic-settings`:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    POSTGRES_DSN: str
    RABBITMQ_URL: str
    PREDICTIONS_QUEUE: str = "predictions"
    PRICES_QUEUE: str = "prices"
    PRICE_REQUESTS_QUEUE: str = "price-requests"
    SCORED_PREDICTIONS_QUEUE: str = "scored-predictions"
    EMIT_PROMETHEUS_METRICS: bool = True
    ACCURACY_METRIC_WINDOW_HOURS: int = 24

settings = Settings()
```

Add `pydantic-settings>=2.2` to `requirements.txt`.

## Acceptance Criteria

1. After `publish_prediction_scored` is called with a valid `ScoringResult`, a `PredictionScored` message appears in the `scored-predictions` queue within 2 seconds.
2. The published message passes `PredictionScored.model_validate_json(raw_bytes)` without validation errors.
3. All required fields are non-null in the published message: `prediction_id`, `asset`, `predicted_direction`, `actual_direction`, `is_correct`, `score`, `scored_at`.
4. After publishing, `outcomes.status = "SCORED"` in Postgres.
5. `GET /metrics` returns HTTP 200 and includes the `prediction_accuracy_rate` gauge line when `EMIT_PROMETHEUS_METRICS=true`.
6. `prediction_accuracy_rate` equals `correct / total` for all predictions scored in the last 24 hours (verified via unit test with seeded DB data).
7. If `total = 0` (no predictions scored in window), `prediction_accuracy_rate = 0.0` and no division-by-zero error occurs.
8. Calling `publish_prediction_scored` for an already-`SCORED` outcome is idempotent: the message is still published (re-publish is acceptable) but the DB status remains `SCORED` with no error.
9. Unit tests in `tests/test_publisher.py` mock `RabbitMQClient.publish` and verify message shape and DB updates.
10. `ruff check` and `mypy` report zero errors on `app/publisher.py` and `app/metrics.py`.

## Implementation Notes

- **Publish-then-commit ordering:** Publish to RabbitMQ before committing the status update. If the DB commit fails after a successful publish, the Credibility Service will process the message but the `outcomes` row will remain `AWAITING_PRICE` or `PENDING`. On retry, the duplicate publish is acceptable because the Credibility Service should be idempotent on `prediction_id`. This is simpler than a two-phase approach and acceptable for V1.
- **contributing_edges serialization:** The `contributing_edges` field is `list[dict]`. Ensure it serializes cleanly to JSON — avoid non-JSON-serializable types inside the dicts (e.g. Python `datetime` objects inside edges must be ISO strings).
- **sources field:** If `outcomes.sources` is `[]`, publish `sources: []`. Do not block publishing because sources are empty.
- **Prometheus thread safety:** `prometheus_client` gauges are thread-safe but the `make_asgi_app()` mount must happen before the FastAPI app starts accepting requests. Place the mount in `main.py` before any route definitions.
- **pydantic-settings:** `pydantic-settings` reads from environment variables automatically. For local development, support a `.env` file via `model_config = SettingsConfigDict(env_file=".env")` in the `Settings` class.
- **Testing the metric:** In `test_publisher.py`, seed the `outcomes` table (in-memory SQLite) with a mix of correct/incorrect `SCORED` rows within the 24h window, then call `update_accuracy_metric` and assert the returned float matches expected. Patch `ACCURACY_GAUGE.set` with `unittest.mock.patch` to avoid Prometheus side effects.
- **RabbitMQ client singleton:** Expose `get_rabbitmq_client()` as a module-level function in `app/main.py` that returns the singleton. Inject it into `publish_prediction_scored` rather than importing directly, to keep the function unit-testable with a mock.

## Definition of Done

- [ ] `src/services/verification/app/publisher.py` exists and is fully type-annotated
- [ ] `src/services/verification/app/metrics.py` exists with `update_accuracy_metric` and `ACCURACY_GAUGE`
- [ ] `src/services/verification/app/config.py` exists with all required settings
- [ ] `src/shared/schemas/messages.py` exports `PredictionScored` with all required fields
- [ ] `GET /metrics` endpoint is mounted and returns Prometheus-format text
- [ ] `tests/test_publisher.py` tests message shape, DB update, and accuracy metric
- [ ] `prediction_accuracy_rate` metric is emitted as both Prometheus gauge and structured log
- [ ] `ruff check src/services/verification/` exits 0
- [ ] `mypy src/services/verification/` exits 0
- [ ] Full end-to-end flow (PredictionMade → window close → score → PredictionScored in queue) verified in Docker Compose
- [ ] `pydantic-settings>=2.2` added to `requirements.txt`
