# T02: Swedish RSS Feed Adapters

## Context

The Ingestion Service (`services/ingestion/`) fetches news from both global (GDELT) and Swedish-language sources. This task builds the RSS feed adapters for the four Swedish news sources: **Dagens Industri** (primary Swedish finance), **Dagens Nyheter**, **Svenska Dagbladet (SvD)**, and **Aftonbladet**. These adapters parse standard RSS 2.0 feeds using the `feedparser` library and return structured `RawArticle` objects. They are consumed by the APScheduler hourly job (S02-T01) alongside the GDELT adapter.

## Background

Swedish news sites publish RSS feeds in RSS 2.0 format. Key challenges:

- **Encoding**: Some feeds declare `charset=latin-1` (ISO-8859-1) in HTTP headers but contain UTF-8 content, or vice versa. `feedparser` handles most cases automatically, but the adapter must validate and re-encode if `chardet` is needed.
- **Feed URLs** (fixed, do not allow overriding via config for MVP):
  - Dagens Industri: `https://www.di.se/rss`
  - Dagens Nyheter: `https://www.dn.se/rss/`
  - SvD: `https://www.svd.se/feed/articles.rss`
  - Aftonbladet: `https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/`
- **Field availability**: RSS entries may omit `description`, `author`, or `published`. Handle all as optional.
- **Language/Country defaults**: All four sources are Swedish-language (`language="sv"`) from Sweden (`country="SE"`). These are hardcoded per adapter instance, not extracted from the feed (feed-level language declarations are unreliable).
- **Body**: RSS `description` is a brief summary (50–200 chars typically). Store it as `body` at this stage. Full body fetching is T03's responsibility and will overwrite this field.

File location: `services/ingestion/adapters/rss.py`

## Inputs

- No runtime parameters. Each adapter instance is configured at construction time with its feed URL and source name.
- Environment variable `RSS_FETCH_TIMEOUT_SECONDS: int` (default: `15`) — HTTP timeout for feed fetch.
- Network access to the four RSS endpoints.

## Outputs

Each adapter instance's `fetch()` method returns `list[RawArticle]` — the same internal dataclass used by the GDELT adapter (defined in `services/ingestion/models.py`):

```python
@dataclass
class RawArticle:
    url: str
    title: str
    body: str | None          # RSS description/summary (may be HTML-stripped later)
    published_at: datetime
    language: str             # hardcoded "sv" for all Swedish sources
    country: str              # hardcoded "SE" for all Swedish sources
    source_name: str          # "di" | "dn" | "svd" | "aftonbladet"
    raw_html: str | None      # None at this stage
    domain: str               # e.g. "di.se"
```

## Technical Requirements

### Libraries
- `feedparser` >= 6.0 — RSS/Atom parsing
- `httpx` — async HTTP fetch (feedparser is sync; call in `asyncio.to_thread`)
- `chardet` — encoding detection fallback
- `datetime`, `email.utils.parsedate_to_datetime` — parse RFC 2822 pubDate
- `urllib.parse.urlparse` — extract domain from URL
- `logging` via `shared.logging`

### Class Interface
```python
# services/ingestion/adapters/rss.py

RSS_SOURCES: dict[str, str] = {
    "di":          "https://www.di.se/rss",
    "dn":          "https://www.dn.se/rss/",
    "svd":         "https://www.svd.se/feed/articles.rss",
    "aftonbladet": "https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/",
}

class RssAdapter:
    def __init__(
        self,
        source_name: str,        # key from RSS_SOURCES
        feed_url: str,
        http_client: httpx.AsyncClient,
    ) -> None: ...

    async def fetch(self) -> list[RawArticle]: ...

def build_all_rss_adapters(http_client: httpx.AsyncClient) -> list[RssAdapter]:
    """Factory: returns one RssAdapter per entry in RSS_SOURCES."""
    ...
```

### Feed Fetch Strategy
1. Use `httpx.AsyncClient` to GET the feed URL with timeout from `RSS_FETCH_TIMEOUT_SECONDS`.
2. Pass the raw response bytes to `feedparser.parse(response.content)`.
3. `feedparser` handles encoding detection from the byte stream — do not pass pre-decoded strings.
4. If `feedparser` sets `feed.bozo = True` AND the entry list is empty, log a WARNING and return `[]`.
5. If `feedparser` sets `feed.bozo = True` but entries are present, log a DEBUG and continue parsing (bozo often fires on minor XML quirks).

### Field Extraction per Entry
| RSS field                 | `RawArticle` field | Transform                                        |
|---------------------------|--------------------|-------------------------------------------------|
| `entry.link`              | `url`              | strip whitespace; skip entry if empty            |
| `entry.title`             | `title`            | strip whitespace; fallback to `url` if missing   |
| `entry.summary` or `entry.description` | `body` | strip HTML tags using `feedparser`'s sanitizer (already done); truncate to 500 chars; `None` if absent |
| `entry.published`         | `published_at`     | parse via `email.utils.parsedate_to_datetime`; fallback to `datetime.now(UTC)` |
| hardcoded `"sv"`          | `language`         | —                                                |
| hardcoded `"SE"`          | `country`          | —                                                |
| constructor `source_name` | `source_name`      | —                                                |
| `urlparse(url).netloc`    | `domain`           | remove `www.` prefix                             |
| `None`                    | `raw_html`         | always None at this stage                        |

### Encoding Normalization
- If `entry.title` contains replacement characters (`�`) after feedparser parsing, attempt re-decode: treat the raw bytes as `latin-1` and decode to UTF-8 via `title.encode("latin-1").decode("utf-8", errors="replace")`.
- Log a DEBUG message when encoding fallback is triggered, including `source_name` and the affected field.

### Error Handling
- HTTP timeout or connection error: log ERROR with source name, return `[]`.
- HTTP 4xx/5xx: log ERROR with status code and source name, return `[]`.
- Entry with missing/empty `link`: skip that entry, log DEBUG.
- `published_at` parse failure: use `datetime.now(timezone.utc)`, log WARNING with entry title.

## Acceptance Criteria

1. `build_all_rss_adapters(http_client)` returns exactly 4 `RssAdapter` instances (one per source).
2. For each adapter, `fetch()` returns a `list[RawArticle]` where every item has non-empty `url` and `title`.
3. All returned `RawArticle` objects have `language="sv"` and `country="SE"`.
4. A unit test using a fixture RSS XML string (minimal valid RSS 2.0 with 3 entries) verifies that all 3 entries are parsed and field mapping is correct.
5. A unit test verifies that an entry with a missing `<link>` is skipped and does not cause an exception.
6. A unit test verifies that HTTP timeout returns `[]` and logs an ERROR.
7. `published_at` is a timezone-aware `datetime` (not naive) for all returned articles.
8. `body` field, when present, contains no raw HTML tags (feedparser strips them) and is ≤ 500 characters.
9. `ruff check services/ingestion/adapters/rss.py` reports zero issues.
10. `mypy --strict services/ingestion/adapters/rss.py` reports zero errors.

## Implementation Notes

- **DI feed quirk**: `di.se/rss` occasionally returns a 301 redirect to a login page when the CDN rate-limits crawlers. The `httpx.AsyncClient` will follow redirects by default (`follow_redirects=True`). If the final response Content-Type is `text/html` instead of `application/rss+xml` or `text/xml`, log a WARNING and return `[]` rather than trying to parse HTML as RSS.
- **Aftonbladet description**: The Aftonbladet feed often includes `<img>` tags in `<description>`. `feedparser` strips these automatically when using `.summary_detail.value` with sanitization enabled. Verify in tests.
- **feedparser is synchronous**: Call it via `await asyncio.to_thread(feedparser.parse, response.content)` to avoid blocking the event loop.
- **Do not use feedparser's built-in HTTP fetching** (passing URL directly to `feedparser.parse`). Always fetch with `httpx` first so you control timeouts and error handling.
- **`bozo_exception` logging**: When `bozo=True`, include `str(feed.bozo_exception)` in the DEBUG log message to aid debugging.
- Aftonbladet URL path ends with a trailing slash — include it exactly as specified, or the CDN returns 404.

## Definition of Done

- [ ] Unit tests pass (`pytest services/ingestion/tests/unit/test_rss_adapters.py`)
- [ ] `ruff check services/ingestion/adapters/rss.py` reports zero issues
- [ ] `mypy --strict services/ingestion/adapters/rss.py` reports zero errors
- [ ] All 4 RSS sources are represented in `RSS_SOURCES` dict with correct URLs
- [ ] `build_all_rss_adapters()` factory function returns 4 adapters
- [ ] Encoding normalization tested with a fixture containing `�` characters
- [ ] HTTP error paths (timeout, 4xx, 5xx, HTML-instead-of-RSS) each have a unit test
- [ ] `published_at` is always timezone-aware (tested explicitly)
- [ ] `feedparser` called via `asyncio.to_thread` (not blocking the event loop)
