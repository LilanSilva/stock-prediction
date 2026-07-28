# T01: GDELT DOC 2.0 Adapter

> **POC-6 replacement requirements (2026-07-13):** GDELT is optional, not the primary availability dependency. Honor `Retry-After`; use bounded exponential backoff with jitter; cache successful query pages; maintain a per-host rate limiter; open a circuit after repeated HTTP 429, invalid-JSON, or transport failures; and let RSS processing complete independently. Remove the fixed `asyncio.sleep(5)` rule and any acceptance criterion requiring GDELT success in every polling cycle.

## Context

The Ingestion Service (`src/services/ingestion/`) is the first stage of the news-driven prediction pipeline. It fetches raw news articles from multiple sources, stores them, and publishes them to the `raw-news` RabbitMQ queue for the Cleansing Service to process. This task implements the adapter for **GDELT DOC 2.0** — the primary English-language source covering financial and geopolitical news globally. The adapter is a standalone, reusable component called by the APScheduler hourly job (built in S02-T01).

## Background

**GDELT DOC 2.0 API** (`https://api.gdeltproject.org/api/v2/doc/doc`) is a free, publicly available news search API requiring no authentication. It indexes global news in near-real-time.

Key design decisions:
- **Rate limit**: GDELT enforces ~1 request per 5 seconds. Violating this returns HTTP 429 or silent errors. Use `asyncio.sleep(5)` between paginated requests.
- **Query strategy**: Filter by theme codes `ECON_*` (economic events) plus geopolitical terms (`war`, `sanctions`, `trade`) to limit volume. Use `mode=ArtList` for article list output, `format=json`.
- **Response shape**: GDELT returns a JSON object with key `articles` — a list of objects each containing `url`, `title`, `seendate`, `domain`, `language`, `sourcecountry`, `socialimage`.
- **No article body**: GDELT only provides metadata + URL. Full body fetching is handled by T03.
- **Last-seen deduplication anchor**: The adapter accepts a `since` datetime parameter so the scheduler (S02-T01) can pass `last_fetched_at` from Postgres to avoid refetching old articles.

File location: `src/services/ingestion/adapters/gdelt.py`

## Inputs

- `since: datetime | None` — optional lower bound on article `seendate` (ISO8601). If `None`, fetch last 24 hours.
- Environment variable `GDELT_MAX_RECORDS: int` (default: `250`) — max articles per poll cycle to prevent runaway volume.
- No API key required.

## Outputs

Returns `list[RawArticle]` — a list of internal dataclass instances (defined in `src/services/ingestion/models.py`).

```python
@dataclass
class RawArticle:
    url: str
    title: str
    body: str | None          # None at this stage; filled by T03
    published_at: datetime
    language: str             # ISO 639-1 e.g. "en"
    country: str              # ISO 3166-1 alpha-2 e.g. "US"
    source_name: str          # e.g. "gdelt"
    raw_html: str | None      # None at this stage
    domain: str               # e.g. "reuters.com"
```

## Technical Requirements

### Libraries
- `httpx` (async HTTP client, already in project deps)
- `asyncio` (standard library)
- `datetime` from standard library
- `logging` (standard library) — use structured logging via `shared.logging`

### API Request Format
```
GET https://api.gdeltproject.org/api/v2/doc/doc
  ?query=ECON OR sanctions OR trade war&
  mode=ArtList&
  maxrecords=250&
  format=json&
  timespan=1440&           # last 24h in minutes, used only when since=None
  startdatetime=YYYYMMDDHHMMSS&  # from since param when provided
  enddatetime=YYYYMMDDHHMMSS
```

### Field Mapping
| GDELT field      | `RawArticle` field  | Transform                              |
|------------------|---------------------|----------------------------------------|
| `url`            | `url`               | strip whitespace                       |
| `title`          | `title`             | strip whitespace; fallback to `domain` |
| `seendate`       | `published_at`      | parse `YYYYMMDDTHHMMSSZ` → UTC datetime|
| `language`       | `language`          | lowercase, take first 2 chars          |
| `sourcecountry`  | `country`           | uppercase; empty string → `"XX"`       |
| `domain`         | `domain`            | as-is                                  |
| literal `"gdelt"`| `source_name`       | hardcoded                              |

### Rate Limiting
- Wrap each HTTP call with `await asyncio.sleep(5)` **before** subsequent paginated requests.
- Single-request polls (no pagination) do not need sleep.
- If HTTP status is 429, wait 30 seconds and retry once. Log warning on retry.

### Error Handling
- On `httpx.TimeoutException`: log error, return empty list (do not crash).
- On HTTP 4xx/5xx (except 429): log error with status code, return empty list.
- On JSON parse error: log error with raw response snippet (first 200 chars), return empty list.
- On missing `articles` key in response: return empty list (GDELT returns `{}` when no results).

### Class Interface
```python
# src/services/ingestion/adapters/gdelt.py
class GdeltAdapter:
    def __init__(self, http_client: httpx.AsyncClient) -> None: ...
    async def fetch(self, since: datetime | None = None) -> list[RawArticle]: ...
```

The `http_client` is injected for testability (allows mocking in unit tests).

## Acceptance Criteria

1. `GdeltAdapter.fetch()` returns a `list[RawArticle]` where every item has non-empty `url`, `title`, and `published_at`.
2. When GDELT returns 0 articles (empty `articles` array or missing key), `fetch()` returns `[]` without raising an exception.
3. When the HTTP call times out, `fetch()` returns `[]` and logs an ERROR message containing `"gdelt"` and `"timeout"`.
4. When `since` is provided, the API request includes `startdatetime` parameter formatted as `YYYYMMDDHHMMSS`.
5. `seendate` strings in format `YYYYMMDDTHHMMSSZ` are correctly parsed into timezone-aware UTC `datetime` objects.
6. `language` field is always lowercase and at most 2 characters (e.g. `"en"`, not `"English"`).
7. `country` field is always uppercase (e.g. `"US"`, `"SE"`).
8. A unit test using `httpx.MockTransport` (or `pytest-httpx`) verifies field mapping from a fixture GDELT JSON response.
9. A unit test verifies that a 429 response triggers a 30-second wait and one retry (mock `asyncio.sleep`).
10. `mypy --strict` reports zero errors on `adapters/gdelt.py`.

## Implementation Notes

- GDELT `seendate` format is `YYYYMMDDTHHMMSSZ` (e.g. `20240115T143000Z`). Use `datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)`.
- GDELT occasionally returns HTML error pages instead of JSON when overloaded — catch `json.JSONDecodeError` and log the first 200 chars of the response body for debugging.
- The `query` parameter should use GDELT theme syntax. Recommended starting query: `"(ECON_BANKRUPTCY OR ECON_INFLATION OR ECON_TRADE OR tax OR sanctions OR \"interest rate\" OR gold OR oil) sourcelang:english"`. Tune this post-MVP based on signal quality.
- Do NOT paginate beyond a single request per poll cycle. GDELT pagination requires `tone` sorting tricks that are fragile. 250 records per hour is sufficient for MVP.
- The adapter must NOT import anything from `src/shared/` except `shared.logging` — it must remain independently testable.
- Store `raw_html=None` and `body=None` — body fetching is T03's responsibility.

## Definition of Done

> Contract-aligned build: adapter path is `ingestion.adapters.gdelt`; tests live in `tests/`.

- [x] Unit tests pass (`pytest tests/test_gdelt_adapter.py`)
- [x] `ruff check` reports zero issues on `ingestion/adapters/gdelt.py`
- [x] `mypy --strict` reports zero errors on `ingestion/adapters/gdelt.py`
- [x] `GdeltAdapter` class is importable from `ingestion.adapters.gdelt`
- [x] All GDELT response fields map correctly to `RawArticle` (country/language names -> ISO2/ISO639)
- [x] HTTP error handling (timeout, 4xx/5xx, 429, invalid JSON) verified by unit tests with mocked responses
- [x] Rate-limit retry enforced (429 test mocks `asyncio.sleep` and asserts one retry)
- [x] `RawArticle` model is defined in `ingestion/models.py` with all fields typed
