"""POC-7 biquote.io market-data probe (standard library only, no third-party deps).

Validates whether biquote.io can replace the Yahoo Finance chart source for the two canonical POC
assets (GOLD, BRENT_OIL) WITHOUT changing any production code. It runs one gate per requirement the
real Market Data adapter depends on and writes a machine-readable result file.

Gates:
  G1  both assets resolve to a biquote symbol that returns daily data (GOLD->XAUUSD, BRENT_OIL->UKOIL)
  G2  a historical date-range query (from/to, interval=1d) returns the bracketing sessions
  G3  the current still-forming bar is flagged isOpen=true so it can be excluded (final-close only)
  G4  bar timestamps are usable for session mapping (record the openTime convention: UTC midnight)
  G5  response shape is understood: no Yahoo-style `meta` block to validate against
  G6  no observable rate limit under a burst (the reason for leaving Yahoo's 429s)

This probe reads only public, key-less endpoints, mutates nothing, and never touches the running
stack, the database, or the frozen policy. A FAIL is a POC finding, not a runtime error.

Usage:
    python probe.py --output results/poc7-biquote.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

BASE_URL = "https://biquote.io/api"
TIMEOUT_SECONDS = 20

# Candidate mapping under test (canonical AssetId -> biquote symbol). GOLD spot vs COMEX futures and
# UKOIL "Crude Oil Brent" vs the Yahoo BZ=F continuous are recorded as economic-identity caveats.
CANDIDATE_SYMBOLS = {
    "GOLD": "XAUUSD",
    "BRENT_OIL": "UKOIL",
}

# A fixed historical window that brackets known trading sessions (kept static: the harness must not
# call datetime.now for the range, so results are reproducible for the doc).
RANGE_FROM = "2026-07-24T00:00:00Z"
RANGE_TO = "2026-07-28T23:59:59Z"


def _get(path: str, params: dict[str, str] | None = None) -> tuple[int, Any]:
    """GET a biquote endpoint; return (http_status, parsed_json_or_none)."""
    url = f"{BASE_URL}/{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 - fixed https host
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return 0, {"error": str(exc)}


def _status_only(path: str, params: dict[str, str]) -> int:
    status, _ = _get(path, params)
    return status


def gate_symbols_have_data() -> dict[str, Any]:
    """G1: each candidate symbol returns at least one daily bar with a positive close."""
    findings: dict[str, Any] = {}
    ok = True
    for asset, symbol in CANDIDATE_SYMBOLS.items():
        status, payload = _get(f"{symbol}/ohlc", {"interval": "1d", "limit": "3"})
        bars = payload.get("bars", []) if isinstance(payload, dict) else []
        has_positive = any(
            isinstance(b, dict) and isinstance(b.get("close"), int | float) and b["close"] > 0
            for b in bars
        )
        findings[asset] = {
            "symbol": symbol,
            "http_status": status,
            "bar_count": len(bars),
            "has_positive_close": has_positive,
            "sample_close": bars[0].get("close") if bars else None,
        }
        ok = ok and status == 200 and has_positive
    return {"gate": "G1_symbols_have_data", "passed": ok, "detail": findings}


def gate_date_range() -> dict[str, Any]:
    """G2: a from/to daily range returns bars, and they fall inside the requested window."""
    findings: dict[str, Any] = {}
    ok = True
    lower = datetime.fromisoformat(RANGE_FROM)
    upper = datetime.fromisoformat(RANGE_TO)
    for asset, symbol in CANDIDATE_SYMBOLS.items():
        status, payload = _get(
            f"{symbol}/ohlc", {"interval": "1d", "from": RANGE_FROM, "to": RANGE_TO}
        )
        bars = payload.get("bars", []) if isinstance(payload, dict) else []
        in_window = True
        times: list[str] = []
        for b in bars:
            ts = b.get("openTime") if isinstance(b, dict) else None
            if not isinstance(ts, str):
                in_window = False
                continue
            times.append(ts)
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if not (lower <= parsed <= upper):
                in_window = False
        findings[asset] = {
            "symbol": symbol,
            "http_status": status,
            "bar_count": len(bars),
            "openTimes": times,
            "all_within_window": in_window,
        }
        ok = ok and status == 200 and len(bars) > 0 and in_window
    return {"gate": "G2_date_range", "passed": ok, "detail": findings}


def gate_final_close_flag() -> dict[str, Any]:
    """G3: the most recent bar exposes isOpen so an unsettled day can be excluded."""
    findings: dict[str, Any] = {}
    ok = True
    for asset, symbol in CANDIDATE_SYMBOLS.items():
        status, payload = _get(f"{symbol}/ohlc", {"interval": "1d", "limit": "3"})
        bars = payload.get("bars", []) if isinstance(payload, dict) else []
        has_flag = all(isinstance(b, dict) and "isOpen" in b for b in bars) and bool(bars)
        newest_is_open = bars[0].get("isOpen") if bars else None
        closed_count = sum(1 for b in bars if isinstance(b, dict) and b.get("isOpen") is False)
        findings[asset] = {
            "symbol": symbol,
            "http_status": status,
            "isOpen_present_on_all_bars": has_flag,
            "newest_bar_isOpen": newest_is_open,
            "closed_bar_count": closed_count,
        }
        ok = ok and status == 200 and has_flag and closed_count >= 1
    return {"gate": "G3_final_close_flag", "passed": ok, "detail": findings}


def gate_timestamp_convention() -> dict[str, Any]:
    """G4: record the bar-time convention (UTC midnight) that session mapping must handle."""
    findings: dict[str, Any] = {}
    ok = True
    for asset, symbol in CANDIDATE_SYMBOLS.items():
        status, payload = _get(f"{symbol}/ohlc", {"interval": "1d", "limit": "2"})
        bars = payload.get("bars", []) if isinstance(payload, dict) else []
        convention = None
        parseable = True
        if bars and isinstance(bars[0], dict):
            ts = bars[0].get("openTime")
            if isinstance(ts, str):
                try:
                    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    convention = {
                        "example": ts,
                        "hour": parsed.hour,
                        "minute": parsed.minute,
                        "tz": "UTC" if ts.endswith("Z") else str(parsed.tzinfo),
                        "is_utc_midnight": parsed.hour == 0 and parsed.minute == 0,
                    }
                except ValueError:
                    parseable = False
        findings[asset] = {"symbol": symbol, "http_status": status, "convention": convention}
        ok = ok and status == 200 and parseable and convention is not None
    return {"gate": "G4_timestamp_convention", "passed": ok, "detail": findings}


def gate_response_shape() -> dict[str, Any]:
    """G5: confirm the response has no Yahoo-style `meta` block (registry validation must change)."""
    status, payload = _get("XAUUSD/ohlc", {"interval": "1d", "limit": "1"})
    top_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    bar_keys = (
        sorted(payload["bars"][0].keys())
        if isinstance(payload, dict) and payload.get("bars")
        else []
    )
    has_meta = "meta" in top_keys
    return {
        "gate": "G5_response_shape",
        "passed": status == 200 and not has_meta and "bars" in top_keys,
        "detail": {
            "http_status": status,
            "top_level_keys": top_keys,
            "bar_keys": bar_keys,
            "has_yahoo_style_meta": has_meta,
        },
    }


def gate_rate_limit(burst: int = 60) -> dict[str, Any]:
    """G6: fire a concurrent burst and confirm no throttling (no 429/non-200)."""
    params = {"interval": "1d", "limit": "1"}
    with ThreadPoolExecutor(max_workers=min(burst, 32)) as pool:
        statuses = list(pool.map(lambda _: _status_only("XAUUSD/ohlc", params), range(burst)))
    counts: dict[str, int] = {}
    for s in statuses:
        counts[str(s)] = counts.get(str(s), 0) + 1
    all_ok = all(s == 200 for s in statuses)
    return {
        "gate": "G6_rate_limit",
        "passed": all_ok,
        "detail": {
            "burst": burst,
            "status_counts": counts,
            "throttled_429": counts.get("429", 0),
        },
    }


def run() -> dict[str, Any]:
    gates = [
        gate_symbols_have_data(),
        gate_date_range(),
        gate_final_close_flag(),
        gate_timestamp_convention(),
        gate_response_shape(),
        gate_rate_limit(),
    ]
    overall = all(g["passed"] for g in gates)
    return {
        "poc": "poc-7-biquote-price-source",
        "base_url": BASE_URL,
        "candidate_symbols": CANDIDATE_SYMBOLS,
        "historical_window": {"from": RANGE_FROM, "to": RANGE_TO},
        "overall_pass": overall,
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="POC-7 biquote.io market-data probe")
    parser.add_argument("--output", default="results/poc7-biquote.json")
    args = parser.parse_args()

    result = run()
    # Stamp run time from the environment, not inside the pure gate logic.
    result["generated_at"] = datetime.now(UTC).isoformat()

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(json.dumps(result, indent=2))
    print(f"\nOverall: {'PASS' if result['overall_pass'] else 'FAIL'} -> {args.output}")
    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
