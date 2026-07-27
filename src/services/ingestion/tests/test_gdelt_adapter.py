import httpx
import pytest

from ingestion.adapters.gdelt import GdeltAdapter, country_to_iso2, language_to_iso639_1
from ingestion.exceptions import AdapterError

SAMPLE = {
    "articles": [
        {
            "url": "https://www.reuters.com/markets/oil-rises",
            "title": "Oil rises on supply concerns",
            "seendate": "20260722T083000Z",
            "domain": "reuters.com",
            "language": "English",
            "sourcecountry": "United Kingdom",
        },
        {
            "url": "https://www.bloomberg.com/news/gold-gains",
            "title": "Gold gains as dollar weakens",
            "seendate": "20260722T090000Z",
            "domain": "bloomberg.com",
            "language": "English",
            "sourcecountry": "United States",
        },
        {
            "url": "",
            "title": "",
            "seendate": "bad-date",
            "domain": "",
            "language": "English",
            "sourcecountry": "United States",
        },
    ]
}


def test_country_and_language_mapping() -> None:
    assert country_to_iso2("United States") == "US"
    assert country_to_iso2("Sweden") == "SE"
    assert country_to_iso2("Nowhereland") == "XX"
    assert language_to_iso639_1("English") == "en"
    assert language_to_iso639_1("Spanish") == "es"
    assert language_to_iso639_1("") == "en"


async def test_fetch_maps_articles_and_skips_malformed() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=SAMPLE))
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = GdeltAdapter(client)
        articles = await adapter.fetch()

    assert len(articles) == 2  # third item (no url/title/bad date) is skipped
    first = articles[0]
    assert first.source_id == "gdelt"
    assert first.title == "Oil rises on supply concerns"
    assert first.language == "en"
    assert first.country == "GB"
    assert first.published_at.year == 2026


async def test_empty_results_returns_empty_list() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await GdeltAdapter(client).fetch() == []


async def test_timeout_raises_adapter_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AdapterError, match="timeout"):
            await GdeltAdapter(client).fetch()


async def test_invalid_json_raises_adapter_error() -> None:
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, content=b"<html>overloaded</html>")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AdapterError):
            await GdeltAdapter(client).fetch()


async def test_429_retries_once_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("ingestion.adapters.gdelt.asyncio.sleep", _no_sleep)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(AdapterError, match="429"):
            await GdeltAdapter(client, retry_wait_seconds=30).fetch()
    assert calls["n"] == 2  # initial + one retry
