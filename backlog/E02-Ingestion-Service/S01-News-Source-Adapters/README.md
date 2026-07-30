# S01: News Source Adapters

> **POC-8 migration (2026-07-30):** the GDELT adapter (T01) is retired and replaced by a
> `FreeNewsApiAdapter` (FreeNewsApi.io) — GDELT returned 0 records under `HTTP 429` IP rate-limiting.
> The new keyed source authenticates via `x-api-key`, searches finance keywords newest-first
> (`in_title` + `order_by=recent`), and pulls **full article bodies** via `/details`. The **four
> Swedish RSS adapters (T02) and the body fetcher (T03) are unchanged.** T01 below is superseded —
> kept for history. See the [epic README](../README.md) banner and
> [POC-8](../../POC/poc-8-freenewsapi-source.md).

## Overview

This story builds the three adapter-layer components that retrieve raw article data from external sources:

1. **GDELT DOC 2.0 Adapter** (T01) — queries the GDELT API for financial and geopolitical news, respecting rate limits.
2. **Swedish RSS Feed Adapters** (T02) — parses four RSS feeds using `feedparser`, handling encoding quirks.
3. **Article Body Fetcher & Normalizer** (T03) — fetches full article bodies via HTTP, extracts clean text, and normalizes to UTF-8.

These adapters are pure data-retrieval components. They return structured Python dataclasses/Pydantic models. They do NOT write to Postgres or publish to RabbitMQ — that is handled by S02.

---

## Tasks

| ID  | Name                                 | Description                                                                    |
|-----|--------------------------------------|--------------------------------------------------------------------------------|
| T01 | GDELT DOC 2.0 adapter                | Fetch financial/geopolitical articles from GDELT API with 1 req/5s rate limit  |
| T02 | Swedish RSS feed adapters            | Parse 4 RSS feeds with feedparser; fix Latin-1/UTF-8 encoding                  |
| T03 | Article body fetcher & normalizer    | Async HTTP fetch + BeautifulSoup extraction + UTF-8 normalize + 2000-char cap  |

---

## Dependencies

- `src/shared/` package must define the `ArticleIngested` Pydantic schema (used downstream in S02, but adapters return an internal `RawArticle` dataclass).
- Python 3.12 environment with `httpx`, `feedparser`, `beautifulsoup4`, `lxml` installed.
- Network access to GDELT API and Swedish news RSS endpoints.

---

## How to Test End-to-End

1. Run `pytest src/services/ingestion/tests/unit/` — all adapter unit tests pass with mocked HTTP responses.
2. Run `pytest src/services/ingestion/tests/integration/ -m live` (requires network) — each adapter returns at least 1 article from its live source.
3. Manually invoke `GdeltAdapter().fetch()` in a Python shell and verify returned objects have non-empty `title`, `url`, `language`, and `country` fields.
4. Manually invoke `ArticleBodyFetcher().fetch_body(url)` with a known DI article URL and verify returned text is UTF-8, ≤ 2000 chars, and contains no HTML tags.
