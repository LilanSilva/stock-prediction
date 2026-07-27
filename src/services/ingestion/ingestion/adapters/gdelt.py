"""GDELT DOC 2.0 adapter (global English news).

GDELT (https://api.gdeltproject.org/api/v2/doc/doc) is a free, no-auth news search API. Per the
POC-6 override it is optional and unreliable, so this adapter raises `AdapterError` on 429 /
transport / invalid-JSON failures (the caller's per-source circuit breaker trips on those) and
returns `[]` only when GDELT genuinely has no results.

GDELT returns country and language *names*, not codes, so they are mapped to ISO 3166-1 alpha-2 /
ISO 639-1 to satisfy the canonical ArticleIngested field rules.
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

SOURCE_ID = "gdelt"

# Common GDELT `sourcecountry` names -> ISO 3166-1 alpha-2. Unmapped/empty -> "XX" (unknown).
_COUNTRY_NAME_TO_ISO2: dict[str, str] = {
    "united states": "US",
    "united kingdom": "GB",
    "canada": "CA",
    "australia": "AU",
    "india": "IN",
    "ireland": "IE",
    "new zealand": "NZ",
    "south africa": "ZA",
    "singapore": "SG",
    "germany": "DE",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "belgium": "BE",
    "switzerland": "CH",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "russia": "RU",
    "ukraine": "UA",
    "china": "CN",
    "japan": "JP",
    "south korea": "KR",
    "hong kong": "HK",
    "taiwan": "TW",
    "brazil": "BR",
    "mexico": "MX",
    "argentina": "AR",
    "saudi arabia": "SA",
    "united arab emirates": "AE",
    "qatar": "QA",
    "israel": "IL",
    "turkey": "TR",
    "egypt": "EG",
    "nigeria": "NG",
    "kenya": "KE",
    "indonesia": "ID",
    "malaysia": "MY",
    "philippines": "PH",
    "thailand": "TH",
    "vietnam": "VN",
    "pakistan": "PK",
}

# Common GDELT `language` names -> ISO 639-1.
_LANGUAGE_NAME_TO_ISO639: dict[str, str] = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "swedish": "sv",
    "russian": "ru",
    "arabic": "ar",
    "chinese": "zh",
    "japanese": "ja",
}


def country_to_iso2(name: str) -> str:
    return _COUNTRY_NAME_TO_ISO2.get(name.strip().lower(), "XX")


def language_to_iso639_1(name: str) -> str:
    key = name.strip().lower()
    if key in _LANGUAGE_NAME_TO_ISO639:
        return _LANGUAGE_NAME_TO_ISO639[key]
    if len(key) >= 2 and key[:2].isalpha():
        return key[:2]
    return "en"


class GdeltAdapter:
    """Fetches global English news metadata from the GDELT DOC 2.0 API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = "https://api.gdeltproject.org/api/v2/doc/doc",
        query: str = "(oil OR gold) sourcelang:english",
        max_records: int = 250,
        timespan: str = "24h",
        retry_wait_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._query = query
        self._max_records = max_records
        self._timespan = timespan
        self._retry_wait_seconds = retry_wait_seconds

    @property
    def source_id(self) -> str:
        return SOURCE_ID

    async def fetch(self, since: datetime | None = None) -> list[RawArticle]:
        params: dict[str, str] = {
            "query": self._query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(self._max_records),
        }
        if since is not None:
            params["startdatetime"] = since.astimezone(UTC).strftime("%Y%m%d%H%M%S")
            params["enddatetime"] = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        else:
            params["timespan"] = self._timespan

        data = await self._request_json(params)
        raw_articles = data.get("articles")
        if not raw_articles:
            return []

        articles: list[RawArticle] = []
        for item in raw_articles:
            article = self._to_article(item)
            if article is not None:
                articles.append(article)
        return articles

    async def _request_json(self, params: dict[str, str]) -> dict[str, Any]:
        response = await self._get(params)
        if response.status_code == 429:
            logger.warning("gdelt_rate_limited", wait_seconds=self._retry_wait_seconds)
            await asyncio.sleep(self._retry_wait_seconds)
            response = await self._get(params)
            if response.status_code == 429:
                raise AdapterError("gdelt still rate-limited after retry (HTTP 429)")

        if response.status_code >= 400:
            raise AdapterError(f"gdelt returned HTTP {response.status_code}")

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("gdelt_invalid_json", snippet=response.text[:200])
            raise AdapterError(f"gdelt returned non-JSON: {exc}") from exc

        return data if isinstance(data, dict) else {}

    async def _get(self, params: dict[str, str]) -> httpx.Response:
        try:
            return await self._client.get(self._base_url, params=params)
        except httpx.TimeoutException as exc:
            logger.error("gdelt_timeout", error=str(exc))
            raise AdapterError(f"gdelt request timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AdapterError(f"gdelt transport error: {exc}") from exc

    def _to_article(self, item: Any) -> RawArticle | None:
        if not isinstance(item, dict):
            return None
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip() or str(item.get("domain") or "").strip()
        seendate = item.get("seendate")
        if not url or not title or not isinstance(seendate, str):
            return None
        try:
            published_at = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            return None
        try:
            return RawArticle(
                source_id=SOURCE_ID,
                url=url,
                title=title,
                summary="",
                published_at=published_at,
                language=language_to_iso639_1(str(item.get("language", ""))),
                country=country_to_iso2(str(item.get("sourcecountry", ""))),
            )
        except ValidationError:
            return None
