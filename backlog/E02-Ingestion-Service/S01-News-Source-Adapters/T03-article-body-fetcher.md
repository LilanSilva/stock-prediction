# T03: Article Body Fetcher & Normalizer

## Context

The Ingestion Service (`src/services/ingestion/`) collects article metadata from GDELT (T01) and RSS feeds (T02), but both sources provide only summaries or no body text at all. This task builds the `ArticleBodyFetcher` component, which takes a list of article URLs, fetches the full HTML page for each, extracts the main article text using `BeautifulSoup`, normalizes it to UTF-8, and truncates to 2000 characters. The enriched body is then stored in Postgres and included in the `ArticleIngested` RabbitMQ message. The body fetcher is called by the hourly scheduler (S02-T01) after deduplication — only new articles (not yet in the `raw_news` table) have their bodies fetched.

## Background

Full-body text dramatically improves Cleansing Service quality (BGE-m3 embeddings, LLM-based event merging). However, fetching article bodies at scale introduces challenges:

- **Paywalls**: DI, DN, and SvD are subscription sites. They return either a truncated teaser or a login redirect for most articles. The fetcher must detect this gracefully and fall back to the RSS summary rather than crashing.
- **Rate / bot detection**: News sites may return 403 or redirect to CAPTCHA pages. Treat any 4xx as a soft failure — keep the RSS summary.
- **Async throughput**: Articles are fetched concurrently using `asyncio.gather` with a semaphore to limit concurrency to 5 simultaneous requests (avoids hammering a single domain).
- **Text extraction heuristics**: `BeautifulSoup` alone is noisy. Use a priority order of CSS selectors known to contain article bodies on Swedish news sites, falling back to `<article>` tag, then `<main>`, then full `<body>` text with `<nav>`, `<footer>`, `<header>`, `<aside>`, `<script>`, `<style>` removed.
- **Paywall detection**: If extracted text is < 150 characters, treat as paywall/failure and return the original RSS summary (passed in as `fallback_body`).

File location: `src/services/ingestion/adapters/body_fetcher.py`

## Inputs

- `articles: list[RawArticle]` — output from T01/T02 adapters; each has `url` and optionally `body` (RSS summary as fallback).
- Environment variable `BODY_FETCH_TIMEOUT_SECONDS: int` (default: `10`).
- Environment variable `BODY_FETCH_CONCURRENCY: int` (default: `5`).
- No API key required.

## Outputs

Returns `list[RawArticle]` — same list with `body` and `raw_html` fields populated where fetching succeeded:

- `body: str` — clean UTF-8 text, ≤ 2000 characters, no HTML tags.
- `raw_html: str | None` — raw HTML response body (stored for audit), truncated at 50,000 characters. `None` if fetch failed.
- Articles where fetch failed retain their original `body` value (RSS summary or `None`).

## Technical Requirements

### Libraries
- `httpx` >= 0.27 — async HTTP, `follow_redirects=True`
- `beautifulsoup4` >= 4.12 with `lxml` parser
- `asyncio` — `asyncio.gather`, `asyncio.Semaphore`
- `chardet` — encoding detection when `Content-Type` charset is absent or wrong
- `logging` via `shared.logging`

### Class Interface
```python
# src/services/ingestion/adapters/body_fetcher.py

class ArticleBodyFetcher:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        concurrency: int = 5,
        timeout_seconds: int = 10,
    ) -> None: ...

    async def enrich(self, articles: list[RawArticle]) -> list[RawArticle]:
        """Fetch and normalize bodies for all articles concurrently.
        Returns a new list; original list is not mutated.
        """
        ...

    async def _fetch_one(self, article: RawArticle) -> RawArticle:
        """Fetch and normalize body for a single article."""
        ...
```

### URL Deduplication (pre-fetch)
Before fetching, deduplicate the input list by `url` — if two `RawArticle` objects share the same URL (possible when GDELT and RSS both index the same article), only fetch once and reuse the result for both.

### Fetch Pipeline per URL
```
1. GET url with httpx, timeout=BODY_FETCH_TIMEOUT_SECONDS, follow_redirects=True
2. If status >= 400: log DEBUG ("paywall or 403"), return article with original body
3. Detect encoding:
   a. Try Content-Type charset header
   b. Fallback: chardet.detect(response.content)["encoding"]
   c. Fallback: "utf-8"
4. Decode response bytes with detected encoding, errors="replace"
5. Store raw HTML (truncated at 50,000 chars) in article.raw_html
6. Parse with BeautifulSoup(html, "lxml")
7. Remove tags: script, style, nav, footer, header, aside, form, button, iframe
8. Try extraction in order:
   a. soup.select_one("article.article-body, div.article-body, div.article__body")
   b. soup.find("article")
   c. soup.find("main")
   d. soup.find("body")
9. Extract text: element.get_text(separator=" ", strip=True)
10. Collapse whitespace: re.sub(r"\s+", " ", text).strip()
11. If len(text) < 150: use fallback_body (original article.body)
12. Truncate to 2000 chars
13. Encode result as UTF-8 (normalize)
14. Return updated RawArticle with body=text, raw_html=raw_html
```

### Concurrency Control
```python
sem = asyncio.Semaphore(self.concurrency)
async def fetch_with_sem(article: RawArticle) -> RawArticle:
    async with sem:
        return await self._fetch_one(article)

results = await asyncio.gather(*[fetch_with_sem(a) for a in deduped], return_exceptions=False)
```

### Headers
Set a browser-like `User-Agent` to reduce 403 rates:
```
User-Agent: Mozilla/5.0 (compatible; FeedAnalyzer/1.0; +https://github.com/your-org/feed-analyzer)
```

### Error Handling
- `httpx.TimeoutException`: log DEBUG with URL, return article with original body.
- `httpx.TooManyRedirects`: log DEBUG with URL, return article with original body.
- `httpx.RequestError` (network error): log WARNING with URL and exception type, return article with original body.
- Any unexpected exception: log ERROR with URL and traceback, return article with original body (never crash the batch).
- All errors are soft failures — the article is still publishable with its RSS summary.

## Acceptance Criteria

1. `ArticleBodyFetcher.enrich([])` returns `[]` without error.
2. Given a mocked HTTP response with a valid HTML page, the returned `RawArticle.body` contains only plain text (no `<` or `>` characters).
3. The returned `body` is ≤ 2000 characters.
4. The returned `body` is valid UTF-8 (test by `body.encode("utf-8")` without exception).
5. When the HTTP response returns status 403, the article retains its original `body` value (RSS summary) and `raw_html` is `None`.
6. When the extracted text is < 150 characters (paywall teaser), the original `body` value is preserved.
7. When two articles in the input share the same URL, the HTTP fetch is performed exactly once (verified by mock call count).
8. No more than `BODY_FETCH_CONCURRENCY` HTTP requests are in-flight simultaneously (verified with semaphore count tracking in tests).
9. An `httpx.TimeoutException` for one article does not prevent other articles from being processed.
10. `ruff check src/services/ingestion/adapters/body_fetcher.py` reports zero issues.
11. `mypy --strict src/services/ingestion/adapters/body_fetcher.py` reports zero errors.

## Implementation Notes

- **lxml vs html.parser**: Use `lxml` parser for BeautifulSoup — it is significantly faster and handles malformed HTML better than the built-in `html.parser`. Ensure `lxml` is in `requirements.txt`.
- **Swedish site CSS selectors**: The priority selectors `article.article-body`, `div.article-body`, `div.article__body` are based on common patterns for Swedish news sites. These will not match perfectly for all sites — the `<article>` and `<main>` fallbacks handle the rest.
- **Raw HTML truncation**: 50,000 characters covers most article pages. This is stored for audit/replay. Do not store full megabyte pages in Postgres.
- **Encoding gotcha**: `httpx` by default decodes response text using the charset from Content-Type. Accessing `response.text` applies this decoding. Instead, use `response.content` (bytes) and decode manually via `chardet` to handle sites with incorrect Content-Type declarations. This is particularly relevant for Aftonbladet.
- **Immutability**: `_fetch_one` should return a new `RawArticle` instance (use `dataclasses.replace(article, body=..., raw_html=...)`) rather than mutating the input. This makes unit testing cleaner.
- **`return_exceptions=False`** in `asyncio.gather`: wrap each task in try/except inside `fetch_with_sem` rather than using `return_exceptions=True`, to keep the return type as `list[RawArticle]` rather than `list[RawArticle | BaseException]`.

## Definition of Done

> Contract-aligned build: fetcher is `ingestion/fetcher.py`; de-duplication is at storage (canonical URL); raw HTML is never stored or published (functional doc sec 2/5).

- [x] Unit tests pass (`pytest tests/test_fetcher.py`)
- [x] `ruff check` reports zero issues on `ingestion/fetcher.py`
- [x] `mypy --strict` reports zero errors on `ingestion/fetcher.py`
- [x] Error/paywall responses raise; the pipeline falls back to the feed summary (tested)
- [ ] Fetcher-level URL dedup — superseded: enforced at storage via the `canonical_url` unique constraint
- [x] Concurrency limit tested (semaphore not exceeded) — `tests/test_pipeline_concurrency.py`
- [x] Body normalized and truncated to <= 2000 chars, UTF-8 safe (`tests/test_normalize.py`)
- [ ] `raw_html` truncated at 50,000 chars — superseded: raw HTML is never stored/published
- [ ] `dataclasses.replace` used — superseded: immutable Pydantic models are used
- [ ] `lxml` in requirements — superseded: not used (feedparser + httpx)
