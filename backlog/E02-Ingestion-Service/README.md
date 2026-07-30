# E02: Ingestion Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

> POC-6 finding: the four RSS feeds passed the live canary; GDELT did not. The walking skeleton must work with an RSS source while GDELT remains optional behind cache, response-aware backoff, and a circuit breaker.

> **POC-8 provider migration (2026-07-30): GDELT retired, replaced by FreeNewsApi.io.** In practice GDELT returned 0 records every poll — its API rate-limits this host's IP with `HTTP 429` (retry-then-skip). POC-8 ([backlog/POC/poc-8-freenewsapi-source.md](../POC/poc-8-freenewsapi-source.md)) validated **FreeNewsApi.io** as a keyed replacement: a documented, header-visible **5,000 requests/day** budget (vs GDELT's opaque throttling), and — unlike GDELT — **full article bodies**. The migration is implemented: `GdeltAdapter` is replaced by `FreeNewsApiAdapter` (`src/services/ingestion/ingestion/adapters/freenewsapi.py`), authenticated via an `x-api-key` header (key in `FREENEWSAPI_KEY`, never committed; empty disables the source). **The four Swedish RSS feeds are unchanged and remain the always-on source pool.** All `gdelt`/GDELT references below are **superseded** — retained for history, non-authoritative. Verified live: `per_source_counts` showed `freenewsapi: 50` new articles with bodies, `gdelt` gone.

## Overview

The Ingestion Service is the entry point of the news-driven stock market prediction pipeline. It polls GDELT (Global Database of Events, Language, and Tone) and four Swedish financial/news RSS feeds on an hourly schedule, normalizes raw articles, deduplicates by URL, stores them in Postgres, and publishes an `ArticleIngested` message per new article to the `raw-news` RabbitMQ queue.

Downstream, the Cleansing Service consumes `raw-news` and performs deduplication, embedding, and event clustering. Nothing else depends on Ingestion directly — the queue decouples them.

---

## Stories

| ID  | Name                              | Description                                                                                    |
|-----|-----------------------------------|-----------------------------------------------------------------------------------------------|
| S01 | News Source Adapters              | Build individual adapters for GDELT DOC 2.0 and four Swedish RSS feeds, plus a body fetcher   |
| S02 | Ingestion Scheduler & Publisher   | APScheduler hourly job, Postgres storage, RabbitMQ publish, and health/monitoring endpoint    |

---

## Architecture Context

### Service Location
`src/services/ingestion/`

### Queues
| Queue      | Role     | Message Schema   |
|------------|----------|-----------------|
| `raw-news` | Producer | `ArticleIngested` |

### Database
| Engine   | Table      | Purpose                                      |
|----------|------------|----------------------------------------------|
| Postgres | `ingestion.articles` | Persist raw articles, canonical-URL dedup anchor, source tracking |
| Postgres | `ingestion.outbox`   | Transactional outbox for `ArticleIngested` publication |

Time-based retention keeps growth bounded: a daily job deletes `ingestion.articles` rows older than
`article_retention_days` (default 30) and `DELIVERED` `ingestion.outbox` rows older than
`outbox_retention_days` (default 7). Safe because feeds only surface recent items, so an aged article
can never be re-ingested. See the ingestion functional document sec 6.1.

### Message Schema: `ArticleIngested`
Defined in `src/shared/` package. Fields:
```
article_id      UUID
source          str          # "gdelt" | "di" | "dn" | "aftonbladet" | "svd"
url             str
title           str
body            str          # up to 2000 chars, UTF-8
published_at    datetime
language        str          # ISO 639-1, e.g. "sv", "en"
country         str          # ISO 3166-1 alpha-2, e.g. "SE", "US"
raw_html        str | None   # stored for audit; may be None on paywall
correlation_id  UUID
```

### External Dependencies
- **GDELT DOC 2.0 API** — `https://api.gdeltproject.org/api/v2/doc/doc` — no API key, rate-limited to 1 req/5s
- **di.se/rss** — Dagens Industri RSS
- **dn.se/rss/** — Dagens Nyheter RSS
- **svd.se/feed/articles.rss** — SvD RSS
- **rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/** — Aftonbladet RSS

---

## Overall Acceptance Criteria

1. Running `docker compose up ingestion` starts the service; the `/health` endpoint returns HTTP 200 within 30 seconds.
2. Within one hour of first start, at least one `ArticleIngested` message appears on the `raw-news` queue.
3. Re-running the hourly job does NOT produce duplicate messages for URLs already present in `raw_news`.
4. All five sources (GDELT + 4 RSS) are polled in each hourly cycle.
5. A failed or unreachable source does not abort the job — other sources still complete.
6. All article bodies are UTF-8 encoded and truncated to ≤ 2000 characters.
7. The `/health` endpoint reports `last_poll_at`, per-source article counts, and consecutive-zero-article alerts.
8. All code passes `ruff` linting and `mypy --strict` type checking with zero errors.
9. Test coverage ≥ 80% on adapter and publisher logic (pytest).
