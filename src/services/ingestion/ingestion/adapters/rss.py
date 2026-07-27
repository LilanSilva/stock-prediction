"""RSS source adapters.

POC-6 canary result: the four Swedish RSS feeds passed availability checks and form the proven
source pool for the M1 walking skeleton. Each adapter fetches an RSS feed and returns normalized
`RawArticle` metadata; article bodies are retrieved separately by the SSRF-safe body fetcher.
"""

from __future__ import annotations

import asyncio
import calendar
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ingestion.exceptions import AdapterError
from ingestion.models import RawArticle


class RssSource(BaseModel):
    """Static configuration for one RSS feed."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    source_id: str = Field(min_length=1)
    feed_url: str = Field(min_length=1)
    language: str = Field(pattern=r"^[a-z]{2}$")
    country: str = Field(pattern=r"^[A-Z]{2}$")


# The four Swedish feeds that passed the POC-6 availability canary.
SWEDISH_SOURCES: tuple[RssSource, ...] = (
    RssSource(source_id="di", feed_url="https://www.di.se/rss", language="sv", country="SE"),
    RssSource(source_id="dn", feed_url="https://www.dn.se/rss/", language="sv", country="SE"),
    RssSource(
        source_id="svd",
        feed_url="https://www.svd.se/feed/articles.rss",
        language="sv",
        country="SE",
    ),
    RssSource(
        source_id="aftonbladet",
        feed_url="https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/",
        language="sv",
        country="SE",
    ),
)


def _struct_time_to_utc(value: struct_time) -> datetime:
    """Convert a feedparser UTC struct_time to a timezone-aware UTC datetime."""
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)


# Some feeds (e.g. dn.se) return HTTP 406 for requests without a browser-like User-Agent/Accept.
_FEED_HEADERS = {
    "user-agent": "feed-analyzer-ingestion/0.1 (+https://github.com/feed-analyzer)",
    "accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.8",
}


class RssAdapter:
    """Fetches and normalizes a single RSS feed into `RawArticle` records."""

    def __init__(self, source: RssSource, client: httpx.AsyncClient) -> None:
        self._source = source
        self._client = client

    @property
    def source_id(self) -> str:
        return self._source.source_id

    async def fetch(self) -> list[RawArticle]:
        """Return the current feed entries as `RawArticle` records.

        Raises `AdapterError` on transport failure; malformed individual entries are skipped so one
        bad item never discards the whole feed.
        """
        try:
            response = await self._client.get(self._source.feed_url, headers=_FEED_HEADERS)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AdapterError(f"rss fetch failed for {self._source.source_id}: {exc}") from exc

        # feedparser is synchronous and CPU-light; run it off the event loop.
        parsed = await asyncio.to_thread(feedparser.parse, response.content)

        articles: list[RawArticle] = []
        for entry in parsed.entries:
            article = self._entry_to_article(entry)
            if article is not None:
                articles.append(article)
        return articles

    def _entry_to_article(self, entry: Any) -> RawArticle | None:
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", None)
        published = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        if not link or not title or not isinstance(published, struct_time):
            return None

        try:
            return RawArticle(
                source_id=self._source.source_id,
                url=link,
                title=title,
                summary=getattr(entry, "summary", "") or "",
                published_at=_struct_time_to_utc(published),
                language=self._source.language,
                country=self._source.country,
            )
        except ValidationError:
            return None
