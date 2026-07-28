# T03: Ingestion Service Health & Monitoring

## Context

The Ingestion Service (`src/services/ingestion/`) runs as a long-lived background process. Operators and automated health checks need visibility into whether the service is functioning — did the last poll succeed? Are all sources returning articles? This task implements:

1. A `GET /health` FastAPI endpoint returning machine-readable status.
2. Structured JSON logging for every poll cycle.
3. An alert log when any source returns 0 articles for 3 consecutive poll cycles.

This is the observability layer used by Docker health checks, Kubernetes liveness probes, and the API Gateway dashboard (which may surface this data).

## Background

**Health endpoint contract**: The endpoint must be fast (< 100ms) — it reads from Postgres, not from live external HTTP calls. It queries the `ingestion_sources` table (populated by S02-T01) for `last_fetched_at` and `consecutive_zero_count` per source.

**Structured logging**: Use Python's standard `logging` module configured to output JSON (via `python-json-logger` or `structlog`). Each log event is a JSON object on a single line — compatible with log aggregation systems (Loki, CloudWatch, Datadog).

**Alert log**: If `consecutive_zero_count >= 3` for any source, emit a log event at ERROR level (not WARNING) so alerting rules can trigger on ERROR log lines. This does not stop the service — it is informational.

**Last hour article count**: The health endpoint reports `articles_last_hour` — count of rows in `raw_news` where `fetched_at >= NOW() - INTERVAL '1 hour'`. This gives operators a quick measure of ingestion volume.

File locations:
- `src/services/ingestion/routers/health.py` — FastAPI router with `/health` endpoint
- `src/services/ingestion/services/health_service.py` — business logic querying DB
- `src/services/ingestion/logging_config.py` — logging setup

## Inputs

- Postgres `ingestion_sources` table (from S02-T01)
- Postgres `raw_news` table (from S02-T02)
- No request body or parameters for `GET /health`

## Outputs

### `GET /health` Response Schema
```json
{
  "status": "ok",
  "checked_at": "2024-01-15T14:30:00Z",
  "last_poll_at": "2024-01-15T14:00:00Z",
  "articles_ingested_last_hour": 47,
  "sources": [
    {
      "name": "gdelt",
      "last_fetched_at": "2024-01-15T14:00:01Z",
      "consecutive_zero_count": 0,
      "status": "ok"
    },
    {
      "name": "di",
      "last_fetched_at": "2024-01-15T14:00:05Z",
      "consecutive_zero_count": 3,
      "status": "alert"
    }
  ]
}
```

**`status` field logic**:
- `"ok"` — all sources have `consecutive_zero_count < 3`
- `"degraded"` — one or more sources have `consecutive_zero_count >= 3`
- HTTP status code is always `200` — never return 503, as that breaks load balancer health checks during legitimate slow polls

**Per-source `status`**:
- `"ok"` — `consecutive_zero_count < 3`
- `"alert"` — `consecutive_zero_count >= 3`
- `"unknown"` — source has never been polled (`last_fetched_at IS NULL`)

### Structured Log: Poll Cycle Start
```json
{"event": "ingestion_poll_start", "timestamp": "...", "sources": ["gdelt", "di", "dn", "svd", "aftonbladet"]}
```

### Structured Log: Poll Cycle End
```json
{
  "event": "ingestion_job_complete",
  "timestamp": "...",
  "articles_fetched_count": 47,
  "new_articles_count": 12,
  "sources_polled": 5,
  "duration_seconds": 12.3,
  "per_source": {"gdelt": 20, "di": 8, "dn": 6, "svd": 5, "aftonbladet": 8}
}
```

### Alert Log: Consecutive Zero Articles
```json
{
  "event": "source_consecutive_zero_alert",
  "level": "ERROR",
  "source": "di",
  "consecutive_zero_count": 3,
  "last_fetched_at": "2024-01-15T14:00:05Z",
  "message": "Source 'di' has returned 0 articles for 3 consecutive polls"
}
```
This log is emitted at the end of each job run where any source has `consecutive_zero_count >= 3`.

## Technical Requirements

### Libraries
- `fastapi` — router and response model
- `pydantic` >= 2.x — `HealthResponse`, `SourceStatus` Pydantic models for response schema
- `sqlalchemy[asyncio]` — async DB queries
- `python-json-logger` >= 2.0 OR `structlog` >= 23.x — structured JSON logging
- Standard `logging` module

### FastAPI Router
```python
# src/services/ingestion/routers/health.py
from fastapi import APIRouter, Depends
from ..services.health_service import HealthService
from ..schemas import HealthResponse

router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def health_check(
    service: HealthService = Depends(get_health_service),
) -> HealthResponse:
    return await service.get_health()
```

### Pydantic Response Models
```python
# src/services/ingestion/schemas.py (add to existing file)
from pydantic import BaseModel
from datetime import datetime

class SourceStatus(BaseModel):
    name: str
    last_fetched_at: datetime | None
    consecutive_zero_count: int
    status: Literal["ok", "alert", "unknown"]

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checked_at: datetime
    last_poll_at: datetime | None
    articles_ingested_last_hour: int
    sources: list[SourceStatus]
```

### Logging Setup
```python
# src/services/ingestion/logging_config.py
import logging
import sys
from pythonjsonlogger import jsonlogger

def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers = [handler]
```

Call `configure_logging()` at the top of `main.py` before the FastAPI app is created.

### Alert Log Emission (in scheduler.py)
After updating `ingestion_sources`, call a check method:
```python
async def _check_and_alert_zero_sources(self, sources_repo: SourcesRepository) -> None:
    sources = await sources_repo.get_all()
    for name, state in sources.items():
        if state.consecutive_zero_count >= 3:
            log.error(
                "source_consecutive_zero_alert",
                source=name,
                consecutive_zero_count=state.consecutive_zero_count,
                last_fetched_at=state.last_fetched_at.isoformat() if state.last_fetched_at else None,
                message=f"Source '{name}' has returned 0 articles for {state.consecutive_zero_count} consecutive polls",
            )
```

### Dev-Only Admin Endpoint
Add a `POST /admin/poll` endpoint that triggers `_run_ingestion_job` immediately (for development/testing). This endpoint must be disabled when `ENVIRONMENT=production` (check env var and return 404).

```python
@router.post("/admin/poll", status_code=202)
async def trigger_poll(scheduler: IngestionScheduler = Depends(...)) -> dict:
    if settings.environment == "production":
        raise HTTPException(status_code=404)
    asyncio.create_task(scheduler._run_ingestion_job())
    return {"status": "triggered"}
```

## Acceptance Criteria

1. `GET /health` returns HTTP 200 with `Content-Type: application/json`.
2. When all sources have `consecutive_zero_count < 3`, response body contains `"status": "ok"`.
3. When any source has `consecutive_zero_count >= 3`, response body contains `"status": "degraded"` and that source's entry has `"status": "alert"`.
4. `articles_ingested_last_hour` in the response matches `SELECT COUNT(*) FROM raw_news WHERE fetched_at >= NOW() - INTERVAL '1 hour'`.
5. `last_poll_at` equals `MAX(last_fetched_at)` across all sources.
6. `GET /health` completes in < 200ms (measured against a local Postgres instance).
7. Every poll cycle produces a `ingestion_job_complete` log line in valid JSON format parseable by `json.loads()`.
8. When a source has `consecutive_zero_count == 3`, an `ERROR`-level log line with `event=source_consecutive_zero_alert` is emitted.
9. `POST /admin/poll` returns HTTP 202 in non-production environments and HTTP 404 in production.
10. `mypy --strict` and `ruff` report zero errors on all files in this task.

## Implementation Notes

- **HTTP 200 always for /health**: Return 200 even when `status="degraded"`. Returning 503 for degraded health causes load balancers and Docker to restart the container in a loop, when the real problem is an external news source outage.
- **DB query for `articles_ingested_last_hour`**: Use `SELECT COUNT(*) FROM raw_news WHERE fetched_at >= NOW() - INTERVAL '1 hour'`. SQLAlchemy Core: `select(func.count()).where(RawNewsORM.fetched_at >= func.now() - text("INTERVAL '1 hour'"))`. This is a fast query given `fetched_at` should have an index.
- **Index on `raw_news.fetched_at`**: Add a B-tree index on `fetched_at` in the migration so the health query does not do a full table scan as the table grows.
- **`last_poll_at` computation**: Compute as `MAX(last_fetched_at)` across all rows in `ingestion_sources` — not from `raw_news`. This reflects the last time the scheduler ran, regardless of whether new articles were found.
- **`python-json-logger` vs `structlog`**: Either works. `python-json-logger` is a thin wrapper over stdlib `logging` and easier to integrate. `structlog` is more powerful. Choose one and use it consistently throughout the service. Do not mix both.
- **Log level from env**: Read `LOG_LEVEL` environment variable (default `"INFO"`). Pass to `configure_logging(level=settings.log_level)`.
- **`/admin/poll` returns 202 Accepted**: The job runs asynchronously; use `asyncio.create_task`. Do not `await` it in the HTTP handler — that would block the response until the hour-long job completes.

## Definition of Done

> Contract-aligned build: `/health` and `/ready` are defined in `app.py`; logging uses the shared structlog setup.

- [x] Health/readiness covered by tests (`tests/test_integration.py`)
- [x] `GET /health` and `/ready` integration test passes against real Postgres + RabbitMQ
- [x] `ruff check` reports zero issues on `ingestion/app.py`
- [x] `mypy --strict` reports zero errors
- [ ] `HealthResponse`/`SourceStatus` Pydantic models — superseded: endpoints return JSON dicts
- [x] Logging outputs valid JSON (shared structlog; covered by shared `test_logging.py`)
- [ ] ERROR alert at `consecutive_empty >= 3` — not implemented (counter tracked; alert threshold is a TODO)
- [ ] `POST /admin/poll` 404 in production — superseded: no admin endpoint (poll runs on startup + schedule)
- [ ] Index on `fetched_at` — superseded: no Alembic; a `content_hash` index is present
- [x] Logging configured at startup (`setup_logging` in the FastAPI lifespan)
