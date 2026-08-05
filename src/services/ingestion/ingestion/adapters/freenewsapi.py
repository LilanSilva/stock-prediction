"""FreeNewsApi.io adapter (keyed global news search; replaces the retired GDELT adapter).

Validated by src/poc/poc8-freenewsapi. FreeNewsApi (https://api.freenewsapi.io/v1) is a keyed REST
news API: authenticate with an `x-api-key` header, search with `in_title`, sort by `recent`, and
fetch full article bodies via `/details`. It replaced GDELT, which returned 0 records under an
undocumented HTTP 429 IP rate-limit; FreeNewsApi exposes a generous, header-visible 5,000/day budget
(`X-RateLimit-*-Day`) that hourly polling never approaches.

Design (matches the GDELT adapter's role — one keyed source alongside the four RSS feeds):
  - The `/news` list returns lightweight items (uuid/title/published_at/publisher) with NO url, so
    each article's `original_url` and `body` are fetched from `/details?uuid=` (one call per item).
  - Keywords are searched newest-first (`order_by=recent`); results are deduped by uuid across
    keywords so an article matching two terms is fetched once.
  - Calls are paced under the documented 2 req/sec cap. Transport / rate-limit / invalid-JSON
    failures raise `AdapterError` (the caller's per-source circuit breaker trips); a genuinely empty
    result returns `[]`.

`languages`/`countries` already arrive as ISO codes (`['en']`, `['SG']`), so mapping is a trivial
take-first-and-validate, unlike GDELT's name-to-code tables. The full body is placed on
`RawArticle.summary`, so it is used as the body fallback when the pipeline's scraper cannot fetch
the page (e.g. publisher 406s).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from pydantic import ValidationError

from ingestion.exceptions import AdapterError
from ingestion.models import RawArticle

logger = structlog.get_logger(__name__)

SOURCE_ID = "freenewsapi"

# Documented throughput cap is 2 req/sec; keep a margin so a poll never self-throttles.
_MIN_REQUEST_INTERVAL_SECONDS = 0.6


def _first_iso2(values: Any, *, upper: bool) -> str | None:
    """Return the first value of an ISO-code list, normalized, if it is a 2-letter code."""
    if not isinstance(values, list) or not values:
        return None
    first = str(values[0]).strip()
    if len(first) < 2 or not first[:2].isalpha():
        return None
    token = first[:2]
    return token.upper() if upper else token.lower()


class FreeNewsApiAdapter:
    """Fetches recent news from FreeNewsApi.io (list + per-article details).

    No keyword pre-filtering: fetching all recent articles and letting the Cleansing service
    classify relevance downstream is more reliable than `in_title` queries, which return HTTP 500
    from the provider.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        base_url: str = "https://api.freenewsapi.io/v1",
        language: str = "en",
        page_size: int = 5,
        default_country: str = "US",
        min_request_interval_seconds: float = _MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._page_size = page_size
        self._default_country = default_country
        self._min_interval = min_request_interval_seconds

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def _pace(self) -> None:
        """Sleep between calls to stay under the documented 2 req/sec cap (0 in tests)."""
        if self._min_interval > 0:
            await asyncio.sleep(self._min_interval)

    async def fetch(self) -> list[RawArticle]:
        """Return recent articles (with bodies from /details). No keyword pre-filtering."""
        items = await self._search()
        articles: list[RawArticle] = []
        for item in items:
            uuid_value = str(item.get("uuid") or "").strip()
            if not uuid_value:
                continue
            await self._pace()
            detail = await self._details(uuid_value)
            article = self._to_article(item, detail)
            if article is not None:
                articles.append(article)
        return articles

    async def _search(self) -> list[dict[str, Any]]:
        params = {
            "language": self._language,
            "order_by": "recent",
            "page_size": str(self._page_size),
        }
        data = await self._request_json("/news", params)
        items = data.get("data")
        if not isinstance(items, list):
            return []
        meta = data.get("meta", {})
        if isinstance(meta, dict):
            logger.debug(
                "freenewsapi_search",
                returned=len(items),
                daily_remaining=meta.get("daily_remaining"),
            )
        return [item for item in items if isinstance(item, dict)]

    async def _details(self, uuid_value: str) -> dict[str, Any]:
        data = await self._request_json("/details", {"uuid": uuid_value})
        detail = data.get("data", data)
        return detail if isinstance(detail, dict) else {}

    async def _request_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        response = await self._get(path, params)
        if response.status_code == 429:
            raise AdapterError("freenewsapi rate-limited (HTTP 429)")
        if response.status_code == 401:
            raise AdapterError("freenewsapi rejected the API key (HTTP 401)")
        if response.status_code >= 400:
            raise AdapterError(f"freenewsapi returned HTTP {response.status_code}")
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("freenewsapi_invalid_json", snippet=response.text[:200])
            raise AdapterError(f"freenewsapi returned non-JSON: {exc}") from exc
        return data if isinstance(data, dict) else {}

    async def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        try:
            return await self._client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"x-api-key": self._api_key},
            )
        except httpx.TimeoutException as exc:
            logger.error("freenewsapi_timeout", error=str(exc))
            raise AdapterError(f"freenewsapi request timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AdapterError(f"freenewsapi transport error: {exc}") from exc

    def _to_article(self, item: dict[str, Any], detail: dict[str, Any]) -> RawArticle | None:
        url = str(detail.get("original_url") or "").strip()
        title = str(item.get("title") or detail.get("title") or "").strip()
        published_raw = str(item.get("published_at") or detail.get("published_at") or "").strip()
        if not url or not title or not published_raw:
            return None
        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00")).astimezone(
                UTC
            )
        except ValueError:
            return None

        language = _first_iso2(detail.get("languages"), upper=False) or self._language
        country = _first_iso2(detail.get("countries"), upper=True) or self._default_country
        body = str(detail.get("body") or detail.get("incipit") or "").strip()
        try:
            return RawArticle(
                source_id=SOURCE_ID,
                url=url,
                title=title,
                summary=body,
                published_at=published_at,
                language=language,
                country=country,
            )
        except ValidationError:
            return None
