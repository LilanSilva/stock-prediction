# T01: APScheduler Hourly Job Setup

## Context

The Ingestion Service (`services/ingestion/`) needs to run its news-fetching pipeline every hour automatically. This task sets up an **APScheduler** `AsyncIOScheduler` within the FastAPI application's lifespan context manager. The scheduled job runs all five source adapters (GDELT + 4 RSS) in parallel, orchestrates body fetching, and passes results to the storage/publish layer (S02-T02). This is the central orchestration layer of the Ingestion Service.

## Background

**APScheduler** (`apscheduler` >= 4.x or 3.x — use 3.x for AsyncIOScheduler stability) schedules async jobs within the event loop. It is started and stopped in the FastAPI `lifespan` async context manager to ensure clean shutdown.

Key design decisions:
- **Parallelism within a job**: All 5 adapters (GDELT + DI + DN + SvD + Aftonbladet) run concurrently via `asyncio.gather`. A failure in one does not block others.
- **last_fetched_at tracking**: Each source has a row in the `ingestion_sources` Postgres table recording the timestamp of the last successful poll. This is passed as the `since` parameter to adapters so they only return articles newer than the last poll.
- **Job overlap prevention**: Use `misfire_grace_time=60` and `coalesce=True` on the trigger to skip missed firings and prevent concurrent job execution.
- **Metrics emission**: After each job run, log structured metrics (`articles_fetched_count`, `sources_polled`, `duration_seconds`) at INFO level.

File locations:
- `services/ingestion/main.py` — FastAPI app + lifespan
- `services/ingestion/scheduler.py` — job definition and orchestration logic
- `services/ingestion/db/sources.py` — `last_fetched_at` read/write

## Inputs

### Environment Variables
| Variable                        | Default         | Description                              |
|---------------------------------|-----------------|------------------------------------------|
| `DATABASE_URL`                  | required        | Postgres DSN, e.g. `postgresql+asyncpg://user:pass@localhost/ingestion` |
| `RABBITMQ_URL`                  | required        | AMQP URL, e.g. `amqp://guest:guest@localhost/` |
| `INGESTION_SCHEDULE_HOUR_INTERVAL` | `1`          | Job interval in hours                    |
| `GDELT_MAX_RECORDS`             | `250`           | Passed to GdeltAdapter                   |
| `BODY_FETCH_TIMEOUT_SECONDS`    | `10`            | Passed to ArticleBodyFetcher             |
| `BODY_FETCH_CONCURRENCY`        | `5`             | Passed to ArticleBodyFetcher             |
| `RSS_FETCH_TIMEOUT_SECONDS`     | `15`            | Passed to RssAdapter                     |

### Postgres Table: `ingestion_sources`
```sql
CREATE TABLE ingestion_sources (
    source_name   VARCHAR(50) PRIMARY KEY,  -- "gdelt", "di", "dn", "svd", "aftonbladet"
    last_fetched_at TIMESTAMPTZ,
    consecutive_zero_count INT NOT NULL DEFAULT 0
);
```
Pre-populated with one row per source on first run (upsert).

## Outputs

- Calls `ArticleStorePublisher.store_and_publish(articles)` (defined in S02-T02) with the merged article list.
- Updates `ingestion_sources.last_fetched_at` and `consecutive_zero_count` after each source's fetch.
- Structured log at INFO level after each job run:
  ```json
  {
    "event": "ingestion_job_complete",
    "articles_fetched_count": 47,
    "sources_polled": 5,
    "duration_seconds": 12.3,
    "per_source": {"gdelt": 20, "di": 8, "dn": 6, "svd": 5, "aftonbladet": 8}
  }
  ```

## Technical Requirements

### Libraries
- `apscheduler` == 3.10.x — `AsyncIOScheduler`, `IntervalTrigger`
- `asyncio` standard library
- `sqlalchemy[asyncio]` + `asyncpg` — async Postgres access
- `httpx` — shared `AsyncClient` instance (created once in lifespan, passed to adapters)
- `logging` via `shared.logging` (structured JSON logs)

### FastAPI Lifespan Pattern
```python
# services/ingestion/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .scheduler import IngestionScheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = IngestionScheduler()
    await scheduler.start()
    yield
    await scheduler.stop()

app = FastAPI(lifespan=lifespan)
```

### Scheduler Setup
```python
# services/ingestion/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

class IngestionScheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    async def start(self) -> None:
        self._scheduler.add_job(
            func=self._run_ingestion_job,
            trigger=IntervalTrigger(hours=INGESTION_SCHEDULE_HOUR_INTERVAL),
            id="ingestion_job",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
            next_run_time=datetime.now(timezone.utc),  # run immediately on startup
        )
        self._scheduler.start()

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def _run_ingestion_job(self) -> None: ...
```

Note: `next_run_time=datetime.now(timezone.utc)` causes the first job to fire immediately on service startup — no waiting for the first hour boundary.

### Job Orchestration
```python
async def _run_ingestion_job(self) -> None:
    start = time.monotonic()
    # 1. Read last_fetched_at per source from Postgres
    source_states = await self.sources_repo.get_all()  # dict[str, SourceState]

    # 2. Run all adapters in parallel
    gdelt_task = gdelt_adapter.fetch(since=source_states["gdelt"].last_fetched_at)
    rss_tasks  = [a.fetch() for a in rss_adapters]  # RSS adapters don't support since=
    all_results = await asyncio.gather(gdelt_task, *rss_tasks, return_exceptions=True)

    # 3. Separate exceptions from results; log any adapter failures
    articles_by_source: dict[str, list[RawArticle]] = {}
    for source, result in zip(source_names, all_results):
        if isinstance(result, Exception):
            log.error("adapter_failed", source=source, error=str(result))
            articles_by_source[source] = []
        else:
            articles_by_source[source] = result

    # 4. Merge all articles, fetch bodies
    all_articles = [a for articles in articles_by_source.values() for a in articles]
    enriched = await body_fetcher.enrich(all_articles)

    # 5. Store + publish (S02-T02)
    await store_publisher.store_and_publish(enriched)

    # 6. Update last_fetched_at + consecutive_zero_count per source
    for source, articles in articles_by_source.items():
        await sources_repo.update_after_fetch(source, len(articles))

    # 7. Log metrics
    duration = time.monotonic() - start
    log.info("ingestion_job_complete", articles_fetched_count=len(enriched), ...)
```

### `ingestion_sources` Repository
```python
# services/ingestion/db/sources.py
class SourcesRepository:
    async def get_all(self) -> dict[str, SourceState]: ...
    async def update_after_fetch(
        self, source_name: str, article_count: int
    ) -> None:
        # Increments consecutive_zero_count if article_count == 0
        # Resets consecutive_zero_count to 0 if article_count > 0
        # Updates last_fetched_at to now()
        ...
```

## Acceptance Criteria

1. Service starts without error and `GET /health` returns HTTP 200 within 10 seconds of container start.
2. The ingestion job fires immediately on startup (within 5 seconds) and then every `INGESTION_SCHEDULE_HOUR_INTERVAL` hours.
3. All 5 adapters are invoked within a single job run (log line per adapter present in structured logs).
4. If one adapter raises an unhandled exception, the other 4 adapters still complete and their articles are processed.
5. `last_fetched_at` in `ingestion_sources` is updated after each job run for all 5 sources.
6. `consecutive_zero_count` increments when a source returns 0 articles and resets when it returns > 0.
7. The job does not run twice concurrently (`coalesce=True` and `misfire_grace_time` are set).
8. The structured log line `ingestion_job_complete` appears in logs after each job run with correct field names.
9. Unit tests for `IngestionScheduler._run_ingestion_job` pass with all dependencies mocked.
10. `mypy --strict` and `ruff` report zero errors on `main.py` and `scheduler.py`.

## Implementation Notes

- **APScheduler 3.x vs 4.x**: APScheduler 4.x introduced breaking changes and is less stable. Pin to `apscheduler==3.10.4` in `requirements.txt`.
- **`next_run_time=datetime.now(timezone.utc)`**: This ensures the job runs on service startup, which is important for development and after container restarts. Without this, you'd wait up to 1 hour for the first run.
- **RSS adapters and `since` parameter**: The RSS feeds do not support `since` filtering at the API level. Pass the full feed to the deduplication layer (S02-T02 uses `ON CONFLICT DO NOTHING` on `url`). This means RSS articles are always fetched in full each cycle — acceptable since RSS feeds typically contain only the 20 most recent items.
- **Shared `httpx.AsyncClient`**: Create a single `httpx.AsyncClient` instance in the lifespan and share it across all adapters. This enables connection pooling. Set `timeout=httpx.Timeout(15.0)` as the default, overridable per-adapter via their own timeout parameters.
- **Database session management**: Use a single `AsyncSession` per job run (not per adapter call). Pass it down, or use a context manager. Commit once at the end of the job.
- **`return_exceptions=True` in asyncio.gather**: Capture adapter exceptions without crashing the gather. Inspect results with `isinstance(result, Exception)` before processing.

## Definition of Done

- [ ] Unit tests pass (`pytest services/ingestion/tests/unit/test_scheduler.py`)
- [ ] `ruff check services/ingestion/main.py services/ingestion/scheduler.py` reports zero issues
- [ ] `mypy --strict services/ingestion/main.py services/ingestion/scheduler.py` reports zero errors
- [ ] `ingestion_sources` table migration exists in `services/ingestion/alembic/versions/`
- [ ] Job fires immediately on startup (verified in integration test or manual test)
- [ ] Adapter failure isolation tested (one mock adapter raises; others' results still processed)
- [ ] `last_fetched_at` updated after job run (integration test queries Postgres)
- [ ] `consecutive_zero_count` increment/reset logic unit tested
- [ ] `apscheduler==3.10.4` pinned in `services/ingestion/requirements.txt`
- [ ] `next_run_time=datetime.now(timezone.utc)` set on the APScheduler job
