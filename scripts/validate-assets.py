#!/usr/bin/env python
"""Validate the JSON asset registry: structure first, then live provider symbols.

Structural validation alone cannot catch a typo'd ticker: the registry loads fine, the asset
predicts, and scoring then stalls silently on PriceNotYetAvailable. This script probes every
``provider_symbol`` against its provider so a bad symbol fails loudly at edit time instead.

Usage:
    python scripts/validate-assets.py                      # structure + live probe
    python scripts/validate-assets.py --offline            # structure only (no network)
    python scripts/validate-assets.py --file infra/assets/assets.json

Exit code is 0 only when every check passes, so this is safe to run in CI or a pre-commit hook.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "shared"))

# Imported after the sys.path insert above so the shared package resolves without installation.
from shared.reference.loader import (
    AssetEntry,
    InvalidRegistryError,
    load_registry,
)

# Yahoo blocks non-browser agents with HTTP 429. This is the root cause of the 429s that retired the
# Yahoo adapter in POC-7 -- it was never IP rate limiting. Adapters must send the same.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT_SECONDS = 25


def _get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _probe_biquote(symbol: str) -> tuple[bool, str]:
    encoded = urllib.parse.quote(symbol, safe="")
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    url = (
        f"https://biquote.io/api/{encoded}/ohlc?interval=1d"
        f"&from={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"&to={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return False, "unexpected payload shape"
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars:
        return False, "0 bars"
    return True, f"{len(bars)} bars"


def _probe_yahoo(symbol: str) -> tuple[bool, str]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=1mo"
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return False, "unexpected payload shape"
    chart = payload.get("chart")
    result = chart.get("result") if isinstance(chart, dict) else None
    if not result:
        return False, "0 bars"
    quote = result[0]["indicators"]["quote"][0]
    closes = [c for c in (quote.get("close") or []) if c is not None]
    if not closes:
        return False, "0 closes"
    meta = result[0].get("meta", {})
    return True, (
        f"{len(closes)} closes, {meta.get('currency')}, {meta.get('exchangeTimezoneName')}"
    )


_PROBES = {"biquote.io": _probe_biquote, "yahoo": _probe_yahoo}


def _check_metadata(entry: AssetEntry, detail: str) -> list[str]:
    """Cross-check the registry's declared currency/timezone against what the provider reported."""
    problems: list[str] = []
    if entry.provider == "yahoo":
        if entry.currency not in detail:
            problems.append(f"declared currency {entry.currency} not in provider metadata")
        if entry.timezone not in detail:
            problems.append(f"declared timezone {entry.timezone} not in provider metadata")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=None, help="registry file (default: packaged)")
    parser.add_argument("--offline", action="store_true", help="skip live provider probes")
    args = parser.parse_args()

    try:
        registry = load_registry(args.file)
    except InvalidRegistryError as exc:
        print(f"FAIL  registry did not load\n      {exc}")
        return 1

    print(f"registry : {args.file or 'packaged default'}")
    print(f"version  : {registry.registry_version}")
    print(f"assets   : {len(registry.assets)}   groups: {len(registry.groups)}")
    print("structure: OK (ids unique, no colons, keywords unambiguous, providers known)")

    if args.offline:
        return 0

    print("\nprobing provider symbols\n" + "-" * 78)
    failures: list[str] = []
    for entry in registry.assets.values():
        probe = _PROBES.get(entry.provider)
        if probe is None:
            print(f"SKIP  {entry.asset_id:<22} no probe for provider {entry.provider!r}")
            continue
        try:
            ok, detail = probe(entry.provider_symbol)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            ok, detail = False, f"transport error: {exc}"
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            ok, detail = False, f"unparseable response: {exc}"

        problems = _check_metadata(entry, detail) if ok else []
        status = "OK  " if ok and not problems else "FAIL"
        print(f"{status}  {entry.asset_id:<22} {entry.provider_symbol:<12} "
              f"{entry.provider:<11} {detail}")
        for problem in problems:
            print(f"        ^ {problem}")
        if not ok:
            failures.append(f"{entry.asset_id} ({entry.provider_symbol} via {entry.provider})")
        failures.extend(f"{entry.asset_id}: {p}" for p in problems)

    print("-" * 78)
    if failures:
        print(f"\n{len(failures)} problem(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(f"\nall {len(registry.assets)} symbols resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
