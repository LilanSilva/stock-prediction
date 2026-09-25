"""Deterministic snapshot identity, freshness, calendar and bounded-failure checks."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from market_data.snapshots.browser import (
    AvanzaBrowser,
    Quote,
    ReadFailure,
    parse_delay,
    parse_price,
    parse_status,
)
from market_data.snapshots.calendar import Session, session_for, slots
from market_data.snapshots.config import Listing, MappingFile, SnapshotSettings, validate_url
from market_data.snapshots.worker import SnapshotWorker, sample_message


@pytest.fixture
def listing(monkeypatch: Any) -> Any:
    monkeypatch.setattr("shared.schemas.asset_id.is_known_asset", lambda x: x == "TEST_STOCK")
    monkeypatch.setattr(Listing, "check_registry", lambda self: "test-registry")
    return Listing(
        asset_id="TEST_STOCK",
        market_code="NASDAQ:TEST",
        search_terms=["TEST"],
        instrument_id="123",
        page_url="https://www.avanza.se/aktier/om-aktien.html/123/test",
        display_name="Test",
        ticker="TEST",
        expected_exchange="NASDAQ",
        page_exchange="NASDAQ",
        expected_currency="USD",
        quote_unit="USD",
        calendar_id="XNYS",
        timezone="America/New_York",
        enabled=True,
        validation_status="VERIFIED",
        validated_at=datetime.now(UTC),
        evidence_url="https://www.avanza.se/aktier/om-aktien.html/123/test",
    )


@pytest.fixture
def settings() -> Any:
    return SnapshotSettings(database_url="unused", rabbitmq_url="unused", retry_seconds=0)


@pytest.fixture
def job(listing: Any) -> Any:
    now = datetime.now(UTC)
    session_window = Session(
        session=now.date(),
        opens_at=now - timedelta(minutes=30),
        closes_at=now + timedelta(hours=1),
        next_open_at=now + timedelta(days=1),
    )
    return {
        "job_id": uuid.uuid4(),
        "asset_id": listing.asset_id,
        "lease": uuid.uuid4(),
        "attempts": 1,
        "listing": listing.model_dump_json(),
        "version": "v1",
        "registry_version": "test-registry",
        "session_window": session_window.model_dump_json(),
        "scheduled_at": now,
        "kind": "REGULAR",
    }


@pytest.mark.parametrize(
    "raw,amount,currency",
    [
        ("377,91 USD", "377.91", "USD"),
        ("1\u00a0234,50 SEK", "1234.50", "SEK"),
        ("99.42 EUR", "99.42", "EUR"),
        ("900,10 DKK", "900.10", "DKK"),
    ],
)
def test_decimal_currency(raw: Any, amount: Any, currency: Any) -> None:
    assert parse_price(raw) == (Decimal(amount), currency)


@pytest.mark.parametrize(
    "raw", ["NaN USD", "-1 SEK", "0 EUR", "100 kr", "100 GBp", "377,91 USD 378 USD", "1.234,50 EUR"]
)
def test_invalid_or_ambiguous_prices(raw: Any) -> None:
    with pytest.raises(ReadFailure):
        parse_price(raw)


def test_market_status_is_not_color_or_timetable() -> None:
    assert (
        parse_status("Marknaden visar efterhandelskurser\nÖppet\n15:30–22:00") == "EXTENDED_HOURS"
    )
    assert parse_status("Marknaden är stängd\nÖppet\n09:00–17:30") == "REGULAR_CLOSED"
    assert parse_status("Marknaden är öppen") == "REGULAR_OPEN"
    assert parse_status("orange\nÖppet\nStängt") == "UNKNOWN"
    assert parse_delay("Du har 15 minuters kursfördröjning") == 900
    assert parse_delay("Kursen uppdateras i realtid") == 0


def test_mapping_disabled_until_verified(listing: Any) -> None:
    with pytest.raises(ValueError):
        Listing.model_validate({**listing.model_dump(), "validation_status": "DRAFT"})
    with pytest.raises(ValueError):
        MappingFile(mapping_version="v1", listings=[listing, listing])
    for url in [
        "http://www.avanza.se/aktier/om-aktien.html/123/test",
        "https://evil.test/aktier/om-aktien.html/123/test",
        "https://www.avanza.se/aktier/om-aktien.html/999/test",
    ]:
        with pytest.raises(ValueError):
            validate_url(url, "123")


def test_calendar_early_close_and_holiday() -> None:
    session_window = session_for(datetime(2026, 11, 27, 15, tzinfo=UTC), "XNYS", "America/New_York")
    assert session_window.closes_at == datetime(2026, 11, 27, 18, tzinfo=UTC)
    regular = [s for s in slots(session_window) if s.kind == "REGULAR"]
    assert len(regular) == 14
    assert regular[-1].scheduled_at == session_window.closes_at - timedelta(minutes=15)
    assert [s.scheduled_at for s in slots(session_window)[-3:]] == [
        session_window.closes_at + timedelta(seconds=s) for s in (0, 120, 300)
    ]
    holiday = session_for(datetime(2026, 11, 26, 15, tzinfo=UTC), "XNYS", "America/New_York")
    assert holiday.opens_at.date().isoformat() == "2026-11-27"
    with pytest.raises(ValueError):
        session_for(datetime.now(UTC), "XNYS", "Europe/Stockholm")
    with pytest.raises(ValueError, match="unsupported exchange calendar"):
        session_for(datetime.now(UTC), "UNKNOWN_TEST_CALENDAR", "America/New_York")


def test_sample_preserves_currency_clock_and_unknown_freshness(job: Any, settings: Any) -> None:
    quote = Quote(
        price=Decimal("100.13"),
        currency="USD",
        observed_at=job["scheduled_at"] + timedelta(seconds=3),
        market_state="REGULAR_OPEN",
        status_text="open",
        quote_delay_seconds=900,
    )
    sample = sample_message(job, quote, settings)
    assert sample.quality == "FRESHNESS_UNKNOWN"
    assert sample.provider_quote_at is None and sample.quote_delay_seconds == 900
    assert json.loads(sample.model_dump_json())["price"] == "100.13"
    assert sample.observed_at != sample.scheduled_at
    assert "high" not in sample.model_dump()


def test_calendar_dst_offsets_follow_exchange_and_close_is_not_a_bar() -> None:
    winter = session_for(datetime(2026, 3, 6, 12, tzinfo=UTC), "XNYS", "America/New_York")
    summer = session_for(datetime(2026, 3, 9, 12, tzinfo=UTC), "XNYS", "America/New_York")
    assert winter.opens_at.hour == 14 and summer.opens_at.hour == 13
    assert len([slot for slot in slots(winter) if slot.kind == "REGULAR"]) == 26
    assert slots(winter)[-1].scheduled_at == winter.closes_at + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_recovered_exhausted_job_never_reads_again(job: Any, settings: Any) -> None:
    store, browser = AsyncMock(), AsyncMock()
    await SnapshotWorker(store, browser, settings).collect({**job, "attempts": 3})
    browser.read.assert_not_called()
    assert store.failure.call_args.args[1] == "ATTEMPTS_EXHAUSTED"


@pytest.mark.asyncio
async def test_one_failure_retries_once_without_saving_price(job: Any, settings: Any) -> None:
    store, browser = AsyncMock(), AsyncMock()
    browser.read.side_effect = TimeoutError()
    worker = SnapshotWorker(store, browser, settings)
    await worker.collect(job)
    assert store.failure.call_args.kwargs["retry"] is True
    store.save.assert_not_called()
    await worker.collect({**job, "attempts": 2})
    assert store.failure.call_args.kwargs["retry"] is False
    store.provider_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_error_pauses_only_mapping(job: Any, settings: Any) -> None:
    store, browser = AsyncMock(), AsyncMock()
    browser.read.side_effect = ReadFailure("CURRENCY_MISMATCH", pause=True)
    await SnapshotWorker(store, browser, settings).collect(job)
    assert store.failure.call_args.kwargs["pause"] is True
    assert store.failure.call_args.kwargs["retry"] is False
    store.control.assert_not_called()


@pytest.mark.asyncio
async def test_throttle_pauses_provider_and_never_retries_immediately(
    job: Any, settings: Any
) -> None:
    store, browser = AsyncMock(), AsyncMock()
    browser.read.side_effect = ReadFailure("RATE_LIMITED", provider=True, retry_after=1200)
    await SnapshotWorker(store, browser, settings).collect(job)
    assert store.control.call_args.kwargs["cooldown"] == 1200
    assert store.failure.call_args.kwargs["retry"] is False


@pytest.mark.asyncio
async def test_expired_slot_does_not_read_a_later_price(job: Any, settings: Any) -> None:
    store, browser = AsyncMock(), AsyncMock()
    job["scheduled_at"] -= timedelta(minutes=16)
    await SnapshotWorker(store, browser, settings).collect(job)
    browser.read.assert_not_called()
    assert store.failure.call_args.args[1] == "MISSED_DEADLINE"


@pytest.mark.asyncio
async def test_sql_retry_preserves_exact_observation(job: Any, settings: Any) -> None:
    store, browser = AsyncMock(), AsyncMock()
    browser.read.return_value = Quote(
        price=Decimal("100"),
        currency="USD",
        observed_at=datetime.now(UTC),
        market_state="REGULAR_OPEN",
        status_text="open",
    )
    store.save.side_effect = [ConnectionError(), True]
    await SnapshotWorker(store, browser, settings).collect(job)
    browser.read.assert_awaited_once()
    assert store.save.await_args_list[0].args[1] == store.save.await_args_list[1].args[1]


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("SNAPSHOT_TEST_BROWSER") != "1", reason="Requires installed Chromium")
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.asyncio
async def test_reads_bypass_warmed_price_cache(
    listing: Any, settings: Any, monkeypatch: Any, tmp_path: Path, persistent: bool
) -> None:
    amount = "100"
    price_requests = 0
    html = """<html><head><title>TEST | Stock</title></head><body>
        <h1>Test</h1><div>NASDAQ</div><div>Aktie</div>
        <span data-e2e="tbs-quote-latest-value"></span>
        <button>Visar om marknadsplatsen</button>
        <h2>Öppettid och realtidskurser</h2><div>Marknaden är öppen</div>
        <script src="/quote.js"></script></body></html>"""

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal price_requests
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            path = request.split(b" ")[1]
            if path == b"/quote.js":
                price_requests += 1
                body = (
                    "document.querySelector('[data-e2e=tbs-quote-latest-value]')"
                    f".textContent='{amount} USD';"
                ).encode()
                content_type = "application/javascript"
            else:
                body, content_type = html.encode(), "text/html; charset=utf-8"
            writer.write(
                (
                    "HTTP/1.1 200 OK\r\nCache-Control: public, max-age=86400\r\n"
                    f"Content-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/stock"
    monkeypatch.setattr("market_data.snapshots.browser.validate_url", lambda *_: None)
    local_listing = listing.model_copy(update={"page_url": url})
    if persistent:
        settings = settings.model_copy(update={"profile_path": tmp_path / "browser-profile"})
    browser = AvanzaBrowser(settings)
    try:
        await browser.start()
        assert browser.context is not None
        # Warm the shared cache without our read safeguards and prove it can serve stale data.
        for expected in ("100 USD", "100 USD"):
            page = await browser.context.new_page()
            await page.goto(url)
            assert await page.locator("[data-e2e=tbs-quote-latest-value]").inner_text() == expected
            await page.close()
            amount = "101"
        assert price_requests == 1
        for expected in ("101", "102"):
            amount = expected
            quote = await browser.read(local_listing)
            assert quote.price == Decimal(expected)
            assert quote.currency == "USD"
        assert price_requests == 3
    finally:
        await browser.close()
        server.close()
        await server.wait_closed()
