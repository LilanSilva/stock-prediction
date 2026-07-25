"""Versioned Yahoo reference-close policy and frozen observation fetcher for P06."""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from poc6 import PRICE_SERIES, _request


REGISTRY_VERSION = "poc6-yahoo-reference-v1"
POLICY = {
    "registry_version": REGISTRY_VERSION,
    "status": "APPROVED_FOR_POC_ONLY",
    "price_kind": "PROVIDER_DAILY_CLOSE",
    "is_adjusted": False,
    "session_policy": {
        "calendar": "YAHOO_RETURNED_FUTURES_SESSIONS",
        "completion_time": "17:00 America/New_York",
        "baseline": "last returned session completed at decision_at",
        "settlement": "next returned session after baseline",
        "holiday_rule": "absence from the frozen provider series means non-session",
    },
    "rollover_policy": {
        "id": "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1",
        "rule": "Include every positive raw close returned by the frozen Yahoo continuous series, including provider-managed contract transitions; perform no adjustment or outcome-based exclusion.",
        "limitation": "The series is a provider-managed continuous reference and is not an official contract settlement history.",
    },
    "fallback": None,
    "assets": {
        "GOLD": {
            "provider": "Yahoo Finance chart",
            "provider_symbol": "GC=F",
            "economic_identity": "COMEX Gold futures continuous reference",
            "expected_exchange": "CMX",
            "timezone": "America/New_York",
            "currency": "USD",
        },
        "BRENT_OIL": {
            "provider": "Yahoo Finance chart",
            "provider_symbol": "BZ=F",
            "economic_identity": "NYMEX Brent Crude Oil Last Day Financial futures continuous reference",
            "expected_exchange": "NYM",
            "timezone": "America/New_York",
            "currency": "USD",
        },
    },
    "evidence": [
        "https://finance.yahoo.com/quote/GC%3DF/",
        "https://finance.yahoo.com/quote/BZ%3DF/",
        "https://www.cmegroup.com/markets/metals/precious/gold.contractSpecs.html",
        "https://www.cmegroup.com/markets/energy/crude-oil/brent-crude-oil-last-day.contractSpecs.html",
    ],
}


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def new_york_offset(local_date: date) -> timedelta:
    """US DST rule used by America/New_York for the POC evaluation period."""
    dst_start = _nth_weekday(local_date.year, 3, 6, 2)
    dst_end = _nth_weekday(local_date.year, 11, 6, 1)
    return timedelta(hours=-4 if dst_start <= local_date < dst_end else -5)


def yahoo_url(symbol: str, start: datetime, end: datetime) -> str:
    encoded = urllib.parse.quote(symbol, safe="")
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?period1={int(start.timestamp())}&period2={int(end.timestamp())}"
        "&interval=1d&events=history"
    )


def fetch_observations(
    asset_id: str,
    start: datetime,
    end: datetime,
    fetched_at: datetime | None = None,
    fetcher: Callable[[str], bytes] = _request,
) -> dict[str, Any]:
    policy = POLICY["assets"][asset_id]
    payload = json.loads(fetcher(yahoo_url(policy["provider_symbol"], start, end)).decode("utf-8"))
    result = payload["chart"]["result"][0]
    metadata = result["meta"]
    if metadata.get("currency") != policy["currency"]:
        raise ValueError(f"Unexpected currency for {asset_id}")
    if metadata.get("exchangeName") != policy["expected_exchange"]:
        raise ValueError(f"Unexpected exchange for {asset_id}: {metadata.get('exchangeName')}")
    if metadata.get("exchangeTimezoneName") != policy["timezone"]:
        raise ValueError(f"Unexpected timezone for {asset_id}")

    timestamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    acquired = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    observations: list[dict[str, Any]] = []
    for timestamp, close in zip(timestamps, closes):
        if close is None or not math.isfinite(float(close)) or float(close) <= 0:
            continue
        provider_bar_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        local_bar_date = (provider_bar_time + new_york_offset(provider_bar_time.date())).date()
        observations.append(
            {
                "asset_id": asset_id,
                "session": local_bar_date.isoformat(),
                "close": float(close),
                "provider_bar_time": provider_bar_time.isoformat(),
                "fetched_at": acquired,
                "source": "Yahoo Finance chart",
                "provider_symbol": policy["provider_symbol"],
                "provider_exchange": metadata.get("exchangeName"),
                "provider_timezone": metadata.get("exchangeTimezoneName"),
                "instrument_type": metadata.get("instrumentType"),
                "currency": metadata.get("currency"),
                "price_kind": POLICY["price_kind"],
                "is_adjusted": POLICY["is_adjusted"],
                "registry_version": REGISTRY_VERSION,
                "rollover_policy": POLICY["rollover_policy"]["id"],
            }
        )
    if len(observations) < 2:
        raise ValueError(f"Insufficient observations for {asset_id}")
    return {"policy": policy, "metadata": metadata, "observations": observations}


def session_completed_at(session: str, timezone_name: str) -> datetime:
    if timezone_name != "America/New_York":
        raise ValueError(f"Unsupported POC timezone: {timezone_name}")
    local_date = date.fromisoformat(session)
    local_naive = datetime.combine(local_date, time(17, 0))
    return (local_naive - new_york_offset(local_date)).replace(tzinfo=timezone.utc)


def resolve_sessions(
    observations: list[dict[str, Any]], decision_at: datetime, timezone_name: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    ordered = sorted(observations, key=lambda item: item["session"])
    completed = [
        item
        for item in ordered
        if session_completed_at(item["session"], timezone_name) <= decision_at
    ]
    if not completed:
        raise ValueError("No completed baseline session")
    baseline = completed[-1]
    baseline_index = ordered.index(baseline)
    if baseline_index + 1 >= len(ordered):
        raise ValueError("No following settlement session")
    return baseline, ordered[baseline_index + 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze P06 market reference observations")
    parser.add_argument("--start", required=True, type=datetime.fromisoformat)
    parser.add_argument("--end", required=True, type=datetime.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path("data/frozen/market-observations.json"))
    parser.add_argument("--policy", type=Path, default=Path("results/reference-price-policy.json"))
    args = parser.parse_args(argv)
    if args.start.tzinfo is None or args.end.tzinfo is None:
        raise ValueError("Start and end must include timezone offsets")

    frozen = {
        asset_id: fetch_observations(asset_id, args.start, args.end)
        for asset_id in PRICE_SERIES
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.policy.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.policy.write_text(json.dumps(POLICY, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "FROZEN",
                "registry_version": REGISTRY_VERSION,
                "observations": {
                    asset: len(value["observations"]) for asset, value in frozen.items()
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
