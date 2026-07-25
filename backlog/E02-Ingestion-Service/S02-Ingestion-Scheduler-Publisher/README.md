# S02: Ingestion Scheduler & Publisher

## Overview

This story wires the source adapters (S01) into a running service. It builds three components:

1. **APScheduler hourly job** (T01) — orchestrates all adapters in parallel every hour, using Postgres `last_fetched_at` to avoid re-fetching seen articles.
2. **Raw article storage & deduplication** (T02) — inserts new articles into the `raw_news` Postgres table (URL-unique) and publishes `ArticleIngested` messages to the `raw-news` RabbitMQ queue.
3. **Health & monitoring endpoint** (T03) — FastAPI `GET /health` endpoint with per-source status, last poll time, and consecutive-zero-article alerting.

All three tasks combine to produce the complete, runnable `services/ingestion/` Docker service.

---

## Tasks

| ID  | Name                                 | Description                                                                                   |
|-----|--------------------------------------|-----------------------------------------------------------------------------------------------|
| T01 | APScheduler hourly job setup         | FastAPI lifespan scheduler, asyncio.gather across all adapters, last_fetched_at tracking       |
| T02 | Raw article storage & deduplication  | Postgres insert (ON CONFLICT DO NOTHING), ArticleIngested publish to raw-news queue            |
| T03 | Ingestion service health & monitoring| GET /health endpoint, structured logging, consecutive-zero-articles alert                      |

---

## Dependencies

- **S01 must be complete**: `GdeltAdapter`, `RssAdapter`, `ArticleBodyFetcher`, and `RawArticle` dataclass must all exist.
- **`src/shared/` package** must provide:
  - `ArticleIngested` Pydantic schema
  - `RabbitMQPublisher` (or equivalent aio-pika wrapper)
  - Structured logging setup
- **Infra running**: Postgres (with `raw_news` table migration applied) and RabbitMQ must be accessible. Use `infra/docker-compose.yml`.
- **Environment variables** set (see T01 for full list).

---

## How to Test End-to-End

1. Start infra: `docker compose -f infra/docker-compose.yml up postgres rabbitmq -d`
2. Apply DB migration: `alembic upgrade head` inside `services/ingestion/`
3. Start the service: `docker compose up ingestion`
4. Call `GET http://localhost:8001/health` — expect HTTP 200 with JSON body containing `status`, `sources`, and `last_poll_at`.
5. Trigger a manual poll: `POST http://localhost:8001/admin/poll` (dev-only endpoint) or wait up to 1 hour for the scheduler.
6. After the first poll:
   - Query `SELECT COUNT(*) FROM raw_news` — expect > 0 rows.
   - Check RabbitMQ management UI (`http://localhost:15672`) — `raw-news` queue should have messages.
7. Trigger the poll a second time — `SELECT COUNT(*) FROM raw_news` should NOT increase for the same URLs.
