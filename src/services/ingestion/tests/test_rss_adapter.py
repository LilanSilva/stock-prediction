import httpx
import pytest

from ingestion.adapters.rss import RssAdapter, RssSource
from ingestion.exceptions import AdapterError

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Dagens Industri</title>
    <item>
      <title>Oljepriset stiger</title>
      <link>https://www.di.se/nyheter/oljepriset-stiger/</link>
      <description>Brent stiger efter beslut.</description>
      <pubDate>Wed, 22 Jul 2026 08:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Guld i fokus</title>
      <link>https://www.di.se/nyheter/guld-i-fokus/</link>
      <description>Guldpriset rör sig.</description>
      <pubDate>Wed, 22 Jul 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Utan datum</title>
      <link>https://www.di.se/nyheter/utan-datum/</link>
      <description>Saknar pubDate.</description>
    </item>
  </channel>
</rss>
"""

SOURCE = RssSource(source_id="di", feed_url="https://www.di.se/rss", language="sv", country="SE")


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_fetch_parses_entries_with_dates() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, content=SAMPLE_RSS.encode()))
    async with _client(transport) as client:
        adapter = RssAdapter(SOURCE, client)
        articles = await adapter.fetch()

    # The dateless third item is skipped; two valid articles remain.
    assert len(articles) == 2
    first = articles[0]
    assert first.source_id == "di"
    assert first.title == "Oljepriset stiger"
    assert str(first.url) == "https://www.di.se/nyheter/oljepriset-stiger/"
    assert first.language == "sv"
    assert first.country == "SE"
    assert first.published_at.tzinfo is not None
    assert first.published_at.year == 2026


async def test_http_error_raises_adapter_error() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(503))
    async with _client(transport) as client:
        adapter = RssAdapter(SOURCE, client)
        with pytest.raises(AdapterError):
            await adapter.fetch()


async def test_source_id_property() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, content=b""))
    async with _client(transport) as client:
        assert RssAdapter(SOURCE, client).source_id == "di"
