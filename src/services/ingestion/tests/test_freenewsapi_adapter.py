"""Tests for the FreeNewsApi.io adapter: list+details flow, dedup, ISO mapping, error handling."""

from __future__ import annotations

import httpx
import pytest

from ingestion.adapters.freenewsapi import FreeNewsApiAdapter
from ingestion.exceptions import AdapterError

# A search list response (lightweight items: uuid/title/published_at, no url).
_LIST = {
    "data": [
        {
            "uuid": "aaaa-1111",
            "title": "Oil rises on supply concerns",
            "published_at": "2026-07-30T08:30:00.000Z",
            "publisher": "Reuters",
        },
        {
            "uuid": "bbbb-2222",
            "title": "Gold gains as dollar weakens",
            "published_at": "2026-07-30T09:00:00.000Z",
            "publisher": "Bloomberg",
        },
    ],
    "meta": {"page_size": 5, "returned": 2},
}

# Per-uuid details responses (add original_url, body, ISO languages/countries).
_DETAILS = {
    "aaaa-1111": {
        "data": {
            "uuid": "aaaa-1111",
            "title": "Oil rises on supply concerns",
            "original_url": "https://www.reuters.com/markets/oil-rises",
            "published_at": "2026-07-30T08:30:00.000Z",
            "body": "Crude oil climbed today as supply concerns mounted across the region.",
            "languages": ["en"],
            "countries": ["GB"],
        }
    },
    "bbbb-2222": {
        "data": {
            "uuid": "bbbb-2222",
            "title": "Gold gains as dollar weakens",
            "original_url": "https://www.bloomberg.com/news/gold-gains",
            "published_at": "2026-07-30T09:00:00.000Z",
            "body": "Gold advanced as the dollar softened.",
            "languages": ["en"],
            "countries": ["US"],
        }
    },
}


def _handler_single_keyword() -> httpx.MockTransport:
    """Route /news to the same list and /details by uuid; only the first keyword yields items."""
    calls = {"news": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/news"):
            calls["news"] += 1
            # Only the first keyword search returns items; the rest are empty (small test).
            return httpx.Response(200, json=_LIST if calls["news"] == 1 else {"data": []})
        if request.url.path.endswith("/details"):
            uuid_value = request.url.params.get("uuid", "")
            return httpx.Response(200, json=_DETAILS.get(uuid_value, {"data": {}}))
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def test_fetch_maps_list_plus_details_to_articles() -> None:
    async with httpx.AsyncClient(transport=_handler_single_keyword()) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil",)
        )
        articles = await adapter.fetch()

    assert len(articles) == 2
    first = articles[0]
    assert first.source_id == "freenewsapi"
    assert str(first.url) == "https://www.reuters.com/markets/oil-rises"
    assert first.title == "Oil rises on supply concerns"
    assert first.language == "en"
    assert first.country == "GB"
    assert "supply concerns" in first.summary  # body carried on summary (body fallback)
    assert first.published_at.year == 2026


async def test_fetch_dedupes_uuid_across_keywords() -> None:
    # Every keyword search returns the same list; each uuid must be fetched/emitted only once.
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/news"):
            return httpx.Response(200, json=_LIST)
        uuid_value = request.url.params.get("uuid", "")
        return httpx.Response(200, json=_DETAILS.get(uuid_value, {"data": {}}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil", "gold")
        )
        articles = await adapter.fetch()

    assert {a.url.__str__() for a in articles} == {
        "https://www.reuters.com/markets/oil-rises",
        "https://www.bloomberg.com/news/gold-gains",
    }
    assert len(articles) == 2  # not 4, despite two keywords both returning both items


async def test_item_without_details_url_is_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/news"):
            return httpx.Response(200, json=_LIST)
        # Details never provide an original_url -> article cannot be built.
        return httpx.Response(200, json={"data": {"body": "x", "languages": ["en"]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil",)
        )
        assert await adapter.fetch() == []


async def test_empty_results_returns_empty_list() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json={"data": []}))
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil",)
        )
        assert await adapter.fetch() == []


async def test_rate_limited_raises_adapter_error() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil",)
        )
        with pytest.raises(AdapterError, match="429"):
            await adapter.fetch()


async def test_invalid_key_raises_adapter_error() -> None:
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(401, json={"error": "invalid"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="bad", min_request_interval_seconds=0, keywords=("oil",)
        )
        with pytest.raises(AdapterError, match="401"):
            await adapter.fetch()


async def test_timeout_raises_adapter_error() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil",)
        )
        with pytest.raises(AdapterError, match="timeout"):
            await adapter.fetch()


async def test_invalid_json_raises_adapter_error() -> None:
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, content=b"<html>overloaded</html>")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = FreeNewsApiAdapter(
            client, api_key="k", min_request_interval_seconds=0, keywords=("oil",)
        )
        with pytest.raises(AdapterError):
            await adapter.fetch()
