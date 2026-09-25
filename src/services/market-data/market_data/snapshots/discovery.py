"""Manual, evidence-based mapping discovery; results are always disabled candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import Page, async_playwright, expect
from shared.reference import resolve
from shared.reference.loader import registry

from market_data.snapshots.browser import AvanzaBrowser, ReadFailure, parse_price
from market_data.snapshots.config import Listing, MappingFile, SnapshotSettings, load_mappings

CALENDARS = {
    "NASDAQ": "XNYS",
    "NYSE": "XNYS",
    "STO": "XSTO",
    "CPH": "XCSE",
    "AMS": "XAMS",
    "ETR": "XETR",
}
SEARCH_EXCHANGES = {
    "NASDAQ": ("NASDAQ",),
    "NYSE": ("NYSE",),
    "STO": ("Stockholmsbörsen",),
    "CPH": ("Köpenhamnsbörsen",),
    "AMS": ("Euronext Amsterdam", "Amsterdam"),
    "ETR": ("Xetra",),
}


async def discover(page: Page, asset_id: str) -> Listing:
    series = resolve(asset_id)
    ticker = series.code.split(":", 1)[1].replace("-", " ")
    if series.expected_exchange not in CALENDARS:
        raise ReadFailure("UNSUPPORTED_EXCHANGE")
    await page.goto("https://www.avanza.se/", wait_until="domcontentloaded")
    cookies = page.get_by_role("button", name="Endast nödvändiga cookies", exact=True)
    if await cookies.is_visible():
        await cookies.click()
    await page.get_by_role("button", name="Sök", exact=True).click()
    await page.locator("#search-input").fill(ticker)
    await page.locator('a[id="list-item-link-0"]').wait_for()
    shares = page.get_by_role("checkbox", name=re.compile(r"^Aktier \("))
    if await shares.count():
        await shares.check()
    candidates: dict[str, str] = {}
    for anchor in await page.locator('a[id^="list-item-link-"]').all():
        label = await anchor.get_attribute("aria-label") or ""
        href = await anchor.get_attribute("href") or ""
        if (
            ". Aktie som handlas på " in label
            and "/aktier/om-aktien.html/" in href
            and re.search(rf"\b{re.escape(series.currency)}\b", label)
            and any(f"handlas på {e}." in label for e in SEARCH_EXCHANGES[series.expected_exchange])
        ):
            name = label.split(". Aktie som handlas", 1)[0]
            if name.endswith(f"({ticker})") or name == ticker:
                candidates[urljoin("https://www.avanza.se", href)] = name
    if len(candidates) != 1:
        raise ReadFailure("AMBIGUOUS" if candidates else "NOT_FOUND")
    url = next(iter(candidates))
    await page.goto(url, wait_until="domcontentloaded")
    price_element = page.locator('[data-e2e="tbs-quote-latest-value"]')
    await expect(price_element).to_have_text(re.compile(r"\d.*[A-Z]{3}"))
    raw = await price_element.inner_text()
    _, currency = parse_price(raw)
    if currency != series.currency:
        raise ReadFailure("CURRENCY_MISMATCH")
    title_ticker = (await page.title()).split("|")[0].strip()
    if title_ticker != ticker:
        raise ReadFailure("TICKER_MISMATCH")
    name = await page.get_by_role("heading", level=1).inner_text()
    body = await page.locator("body").inner_text()
    exchange = re.search(r"\n([^\n]+)\n\|\n(Aktie|Depåbevis)\n", body)
    instrument = re.search(r"/om-aktien.html/(\d+)/", page.url)
    if not exchange or not instrument:
        raise ReadFailure("IDENTITY_NOT_VISIBLE")
    return Listing(
        asset_id=asset_id,
        market_code=series.code,
        search_terms=[ticker],
        instrument_id=instrument[1],
        page_url=page.url,
        display_name=name,
        ticker=ticker,
        expected_exchange=series.expected_exchange,
        page_exchange=exchange[1],
        expected_currency=currency,
        quote_unit=currency,
        timezone=series.timezone,
        calendar_id=CALENDARS[series.expected_exchange],
        instrument_type="STOCK" if exchange[2] == "Aktie" else "DEPOSITARY_RECEIPT",
        enabled=False,
        validation_status="VERIFIED",
        validated_at=datetime.now(UTC),
        evidence_url=page.url,
    )


async def main_async(args: argparse.Namespace) -> None:
    if args.command == "validate":
        config = load_mappings(args.file)
        print(
            json.dumps(
                {
                    "version": config.mapping_version,
                    "listings": len(config.listings),
                    "enabled": sum(x.enabled for x in config.listings),
                }
            )
        )
        return
    if args.command == "probe":
        config = load_mappings(args.file)
        settings = SnapshotSettings(
            database_url="unused", rabbitmq_url="unused", browser_channel=args.channel
        )
        browser = AvanzaBrowser(settings)
        report = []
        try:
            for listing in config.listings:
                try:
                    quote = await browser.read(listing)
                    report.append({"asset_id": listing.asset_id, **quote.model_dump(mode="json")})
                except Exception as exc:
                    report.append(
                        {
                            "asset_id": listing.asset_id,
                            "error": exc.code
                            if isinstance(exc, ReadFailure)
                            else type(exc).__name__,
                        }
                    )
        finally:
            await browser.close()
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"probed": len(report), "report": str(args.output)}))
        return
    # Enumeration uses the canonical loader, not guessed company lists.
    assets = args.asset or list(registry().assets)
    listings = []
    errors = []
    async with async_playwright() as p:
        discovery_browser = await p.chromium.launch(headless=True, channel=args.channel)
        try:
            page = await discovery_browser.new_page(locale="sv-SE")
            page.set_default_timeout(15000)
            for asset in assets:
                try:
                    listings.append(await discover(page, asset))
                    print(json.dumps({"asset": asset, "result": "VERIFIED_DISABLED"}), flush=True)
                except Exception as exc:
                    errors.append(
                        {
                            "asset_id": asset,
                            "error": exc.code
                            if isinstance(exc, ReadFailure)
                            else type(exc).__name__,
                        }
                    )
                    print(json.dumps(errors[-1]), flush=True)
        finally:
            await discovery_browser.close()
    config = MappingFile(mapping_version=args.version, listings=listings)
    args.output.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".report.json").write_text(
        json.dumps({"unresolved": errors}, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    find = commands.add_parser("discover")
    find.add_argument("--asset", action="append")
    find.add_argument("--output", type=Path, required=True)
    find.add_argument("--version", required=True)
    find.add_argument("--channel", default=None)
    validate = commands.add_parser("validate")
    validate.add_argument("file", type=Path)
    probe = commands.add_parser("probe")
    probe.add_argument("file", type=Path)
    probe.add_argument("--channel", default=None)
    probe.add_argument("--output", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
