"""Avanza DOM reader. Only the regular last-price element is sampled, never chart history."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Literal

from playwright.async_api import BrowserContext, Playwright, async_playwright, expect
from playwright.async_api import TimeoutError as BrowserTimeoutError
from pydantic import BaseModel

from market_data.snapshots.config import Listing, SnapshotSettings, validate_url


class ReadFailure(Exception):
    def __init__(
        self, code: str, *, pause: bool = False, provider: bool = False, retry_after: int = 0
    ) -> None:
        super().__init__(code)
        self.code, self.pause, self.provider = code, pause, provider
        self.retry_after = retry_after


MarketState = Literal[
    "PRE_OPEN", "REGULAR_OPEN", "REGULAR_CLOSED", "EXTENDED_HOURS", "HALTED", "UNKNOWN"
]


class Quote(BaseModel):
    price: Decimal
    currency: str
    observed_at: datetime
    provider_quote_at: datetime | None = None
    quote_delay_seconds: int | None = None
    market_state: MarketState
    status_text: str


def parse_price(text: str) -> tuple[Decimal, str]:
    normalized = re.sub(r"[\s\u00a0\u202f]+", " ", text).strip()
    match = re.fullmatch(r"([0-9]+(?: [0-9]{3})*(?:[.,][0-9]+)?) ([A-Z]{3})", normalized)
    if not match:
        raise ReadFailure("PARSE_FAILED")
    value = Decimal(match[1].replace(" ", "").replace(",", "."))
    if not value.is_finite() or value <= 0:
        raise ReadFailure("INVALID_PRICE")
    return value, match[2]


def parse_status(text: str) -> MarketState:
    # Match the current-state heading, not the table listing all possible trading states.
    heading = text.casefold().split("\n")
    if any("handelsstopp" in line or "handeln är stoppad" in line for line in heading):
        return "HALTED"
    if any(
        "marknaden visar efterhandelskurser" in line or "marknaden är öppen för efterhandel" in line
        for line in heading
    ):
        return "EXTENDED_HOURS"
    if any(
        "marknaden visar förhandelskurser" in line or "marknaden är öppen för förhandel" in line
        for line in heading
    ):
        return "PRE_OPEN"
    if any(line.strip() in ("marknaden är stängd", "börsen är stängd") for line in heading):
        return "REGULAR_CLOSED"
    if any(line.strip() in ("marknaden är öppen", "börsen är öppen") for line in heading):
        return "REGULAR_OPEN"
    return "UNKNOWN"


def parse_delay(text: str) -> int | None:
    match = re.search(r"(\d+) minuters kursfördröjning", text.casefold())
    if match:
        return int(match[1]) * 60
    if "kursen uppdateras i realtid" in text.casefold():
        return 0
    return None


def retry_seconds(value: str | None, now: datetime) -> int:
    if not value:
        return 900
    try:
        return max(1, int(value))
    except ValueError:
        try:
            return max(1, int((parsedate_to_datetime(value) - now).total_seconds()))
        except (ValueError, TypeError):
            return 900


class AvanzaBrowser:
    def __init__(self, settings: SnapshotSettings) -> None:
        self.settings = settings
        self.runtime: Playwright | None = None
        self.context: BrowserContext | None = None
        self.lock = asyncio.Lock()
        self.semaphore = asyncio.Semaphore(settings.concurrency)

    async def start(self) -> None:
        async with self.lock:
            if self.context is not None:
                return
            self.runtime = await async_playwright().start()
            try:
                if self.settings.profile_path:
                    self.context = await self.runtime.chromium.launch_persistent_context(
                        str(self.settings.profile_path),
                        headless=True,
                        channel=self.settings.browser_channel,
                        locale="sv-SE",
                        service_workers="block",
                    )
                else:
                    browser = await self.runtime.chromium.launch(
                        headless=True,
                        channel=self.settings.browser_channel,
                    )
                    self.context = await browser.new_context(
                        locale="sv-SE", service_workers="block"
                    )
                self.context.set_default_timeout(self.settings.timeout_seconds * 1000)
            except BaseException:
                await self.runtime.stop()
                self.runtime = None
                raise

    async def close(self) -> None:
        async with self.lock:
            try:
                if self.context:
                    await self.context.close()
                if self.runtime:
                    await self.runtime.stop()
            finally:
                self.context, self.runtime = None, None

    async def read(self, listing: Listing) -> Quote:
        async with self.semaphore:
            await self.start()
            assert self.context is not None
            page = await self.context.new_page()
            navigated = False
            try:
                # New tabs share the context's cache. Bypass it per tab before any request,
                # without clearing shared state underneath other concurrent reads.
                network = await self.context.new_cdp_session(page)
                await network.send("Network.enable")
                await network.send("Network.setCacheDisabled", {"cacheDisabled": True})
                await network.send("Network.setBypassServiceWorker", {"bypass": True})
                response = await page.goto(listing.page_url, wait_until="domcontentloaded")
                navigated = True
                if response and response.status == 429:
                    raise ReadFailure(
                        "RATE_LIMITED",
                        provider=True,
                        retry_after=retry_seconds(
                            response.headers.get("retry-after"), datetime.now(UTC)
                        ),
                    )
                if response and response.status in (401, 403):
                    raise ReadFailure("ACTION_REQUIRED", pause=True, provider=True)
                if response and response.status == 404:
                    raise ReadFailure("LISTING_NOT_FOUND", pause=True)
                if response and response.status >= 500:
                    raise ReadFailure("PROVIDER_UNAVAILABLE")
                if re.search(
                    r"captcha|access denied|verify you are human", await page.title(), re.I
                ):
                    raise ReadFailure("ACTION_REQUIRED", pause=True, provider=True)
                try:
                    validate_url(page.url, listing.instrument_id)
                except ValueError as exc:
                    raise ReadFailure("LISTING_REDIRECT", pause=True) from exc
                await page.get_by_role("heading", name=listing.display_name, exact=True).wait_for()
                title = await page.title()
                if title.split("|")[0].strip() != listing.ticker:
                    raise ReadFailure("IDENTITY_MISMATCH", pause=True)
                if not await page.get_by_text(listing.page_exchange, exact=True).count():
                    raise ReadFailure("EXCHANGE_MISMATCH", pause=True)
                instrument = "Aktie" if listing.instrument_type == "STOCK" else "Depåbevis"
                if not await page.get_by_text(instrument, exact=True).count():
                    raise ReadFailure("INSTRUMENT_TYPE_MISMATCH", pause=True)
                cookies = page.get_by_role("button", name="Endast nödvändiga cookies", exact=True)
                if await cookies.is_visible():
                    await cookies.click()
                # This semantic selector was verified on the live stock page. It excludes the
                # independent extended-hours component and comparison series.
                price_element = page.locator('[data-e2e="tbs-quote-latest-value"]')
                await expect(price_element).to_have_text(re.compile(r"\d.*[A-Z]{3}"))
                raw = await price_element.inner_text()
                price, currency = parse_price(raw)
                observed = datetime.now(UTC)
                if currency != listing.expected_currency:
                    raise ReadFailure("CURRENCY_MISMATCH", pause=True)
                await page.get_by_role("button", name="Visar om marknadsplatsen").click()
                await page.get_by_role("heading", name="Öppettid och realtidskurser").wait_for()
                body = await page.locator("body").inner_text()
                status = body.rsplit("Öppettid och realtidskurser", 1)[-1].strip()
                return Quote(
                    price=price,
                    currency=currency,
                    observed_at=observed,
                    market_state=parse_status(status),
                    status_text=status[:2000],
                    quote_delay_seconds=parse_delay(status),
                )
            except (BrowserTimeoutError, AssertionError) as exc:
                raise ReadFailure("PARSE_FAILED" if navigated else "FETCH_FAILED") from exc
            finally:
                await page.close()
