"""POC-8 FreeNewsApi.io ingestion-source probe (standard library only, no third-party deps).

Evaluates whether FreeNewsApi.io (https://api.freenewsapi.io) can serve as a keyed news source
alongside/instead of GDELT, which currently returns 0 records under HTTP 429 IP rate-limiting.

The API key is read from the FREENEWSAPI_KEY environment variable and is NEVER written to disk,
logged, or embedded in the results file. Results record only observed behaviour (status codes,
rate-limit headers, article counts) — never the key.

Gates:
  G1  authenticated fetch works (x-api-key header -> 200) and rate-limit headers are exposed
  G2  keyword search for the ingestion terms (oil/gold/OPEC/sanctions/inflation) returns articles
  G3  the response maps to the ArticleIngested contract (title, url, published_at, language, body)
  G4  RATE LIMIT: paced requests at <=2 req/sec all succeed; the daily budget is read from headers
  G5  RATE LIMIT: a deliberate over-speed burst characterises the throttle (429 + any Retry-After)

This probe reads only its own key's data, mutates nothing, and never touches the running stack. It
is intentionally frugal with quota (about a dozen calls total against a 5,000/day budget).

Usage:
    FREENEWSAPI_KEY=... python probe.py --output results/poc8-freenewsapi.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

BASE_URL = "https://api.freenewsapi.io/v1"
TIMEOUT_SECONDS = 25

# The canonical ingestion keywords (mirrors the GDELT query the service runs today).
KEYWORDS = ["oil", "gold", "OPEC", "sanctions", "inflation"]

# Header names the API exposes for its limits (observed on a probe call).
_RATE_HEADER_KEYS = (
    "X-RateLimit-Limit-Day",
    "X-RateLimit-Remaining-Day",
    "X-RateLimit-Reset-Day",
    "Retry-After",
)


def _key() -> str:
    key = os.environ.get("FREENEWSAPI_KEY")
    if not key:
        print("FREENEWSAPI_KEY not set", file=sys.stderr)
        raise SystemExit(2)
    return key


def _get(path: str) -> tuple[int, dict[str, str], Any]:
    """GET a path; return (status, selected_rate_headers, parsed_json_or_none)."""
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"x-api-key": _key()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310 - fixed https host
            headers = {k: resp.headers.get(k, "") for k in _RATE_HEADER_KEYS if resp.headers.get(k)}
            return resp.status, headers, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        headers = {k: exc.headers.get(k, "") for k in _RATE_HEADER_KEYS if exc.headers.get(k)}
        return exc.code, headers, None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return 0, {}, {"error": str(exc)}


def gate_authenticated_fetch() -> dict[str, Any]:
    """G1: an authenticated list call returns 200 and exposes rate-limit headers."""
    status, headers, _ = _get("/news?in_title=oil&language=en&page_size=3")
    return {
        "gate": "G1_authenticated_fetch",
        "passed": status == 200 and bool(headers),
        "detail": {"http_status": status, "rate_headers": headers},
    }


def gate_keyword_search() -> dict[str, Any]:
    """G2: each ingestion keyword returns at least one article (paced at 2/sec)."""
    findings: dict[str, Any] = {}
    ok = True
    for i, kw in enumerate(KEYWORDS):
        if i:
            time.sleep(0.6)  # stay under 2 req/sec
        status, _, payload = _get(f"/news?in_title={kw}&language=en&page_size=3")
        data = payload.get("data", []) if isinstance(payload, dict) else []
        findings[kw] = {"http_status": status, "returned": len(data)}
        ok = ok and status == 200 and len(data) > 0
    return {"gate": "G2_keyword_search", "passed": ok, "detail": findings}


def gate_contract_mapping() -> dict[str, Any]:
    """G3: list + details expose the fields ArticleIngested needs (title, url, published_at, body)."""
    status, _, payload = _get("/news?in_title=gold&language=en&page_size=2")
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not data:
        return {"gate": "G3_contract_mapping", "passed": False, "detail": {"list_status": status}}
    uuid = data[0]["uuid"]
    time.sleep(0.6)
    dstatus, _, detail_payload = _get(f"/details?uuid={uuid}")
    art = detail_payload.get("data", detail_payload) if isinstance(detail_payload, dict) else {}
    required = {"title", "original_url", "published_at", "languages", "body"}
    present = required.issubset(art.keys()) if isinstance(art, dict) else False
    body_len = len(art.get("body", "") or "") if isinstance(art, dict) else 0
    return {
        "gate": "G3_contract_mapping",
        "passed": dstatus == 200 and present and body_len > 0,
        "detail": {
            "list_item_fields": sorted(data[0].keys()),
            "detail_fields": sorted(art.keys()) if isinstance(art, dict) else [],
            "required_present": present,
            "body_length": body_len,
        },
    }


def gate_paced_rate_limit() -> dict[str, Any]:
    """G4: paced requests (<=2/sec) all succeed; read the daily budget from headers."""
    statuses: list[int] = []
    day_limit = day_remaining = None
    for i in range(5):
        if i:
            time.sleep(0.6)
        status, headers, _ = _get("/news?in_title=oil&language=en&page_size=1")
        statuses.append(status)
        if headers.get("X-RateLimit-Limit-Day"):
            day_limit = headers["X-RateLimit-Limit-Day"]
            day_remaining = headers.get("X-RateLimit-Remaining-Day")
    return {
        "gate": "G4_paced_rate_limit",
        "passed": all(s == 200 for s in statuses),
        "detail": {
            "paced_statuses": statuses,
            "daily_limit": day_limit,
            "daily_remaining": day_remaining,
        },
    }


def gate_burst_rate_limit(burst: int = 10) -> dict[str, Any]:
    """G5: a deliberate no-delay burst characterises the per-second throttle (429 + Retry-After).

    This gate does NOT require 200s — it PASSES as long as the service degrades gracefully (only
    200/429, no 5xx), documenting how the throttle presents so the adapter can respect it.
    """
    statuses: list[int] = []
    retry_after: str | None = None
    for _ in range(burst):
        status, headers, _ = _get("/news?in_title=oil&language=en&page_size=1")
        statuses.append(status)
        if headers.get("Retry-After"):
            retry_after = headers["Retry-After"]
    counts: dict[str, int] = {}
    for s in statuses:
        counts[str(s)] = counts.get(str(s), 0) + 1
    graceful = all(s in (200, 429) for s in statuses)
    return {
        "gate": "G5_burst_rate_limit",
        "passed": graceful,
        "detail": {
            "burst": burst,
            "status_counts": counts,
            "throttled_429": counts.get("429", 0),
            "retry_after": retry_after,
        },
    }


def run() -> dict[str, Any]:
    gates = [
        gate_authenticated_fetch(),
        gate_keyword_search(),
        gate_contract_mapping(),
        gate_paced_rate_limit(),
        gate_burst_rate_limit(),
    ]
    return {
        "poc": "poc-8-freenewsapi-source",
        "base_url": BASE_URL,
        "keywords": KEYWORDS,
        "overall_pass": all(g["passed"] for g in gates),
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="POC-8 FreeNewsApi.io probe")
    parser.add_argument("--output", default="results/poc8-freenewsapi.json")
    args = parser.parse_args()

    result = run()
    result["generated_at"] = datetime.now(UTC).isoformat()

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(json.dumps(result, indent=2))
    print(f"\nOverall: {'PASS' if result['overall_pass'] else 'FAIL'} -> {args.output}")
    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
