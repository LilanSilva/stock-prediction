"""Cached, rate-aware GDELT metadata collector for the P06 historical corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from collector import article_identity, canonicalize_url, content_hash, load_corpus, save_corpus
from poc6 import USER_AGENT


GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_QUERIES = {
    "risk_supply": "(\"Iran war\" OR \"Ukraine war\" OR \"military attack\" OR \"missile strike\" OR \"drone strike\" OR sanctions OR \"oil supply\" OR pipeline) sourcelang:english",
    "macro_rates": "(\"rate hike\" OR \"rate cut\" OR \"rates raised\" OR \"rates lowered\" OR \"inflation rises\" OR \"inflation falls\" OR recession) sourcelang:english",
}


def gdelt_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


class GdeltClient:
    def __init__(
        self,
        cache_dir: Path,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = 6.0,
        max_attempts: int = 6,
    ) -> None:
        self.cache_dir = cache_dir
        self.opener = opener
        self.sleep = sleep
        self.clock = clock
        self.min_interval_seconds = min_interval_seconds
        self.max_attempts = max_attempts
        self.next_request_at = 0.0
        self.consecutive_failures = 0
        self.circuit_open = False

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"

    def is_cached(self, url: str) -> bool:
        return self._cache_path(url).exists()

    def get_json(self, url: str) -> dict[str, Any]:
        cache_path = self._cache_path(url)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        if self.circuit_open:
            raise RuntimeError("GDELT circuit is open after repeated failures")

        for attempt in range(self.max_attempts):
            wait_for_slot = max(0.0, self.next_request_at - self.clock())
            if wait_for_slot:
                self.sleep(wait_for_slot)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with self.opener(request, timeout=45) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.next_request_at = self.clock() + self.min_interval_seconds
                self.consecutive_failures = 0
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                temporary = cache_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                temporary.replace(cache_path)
                return payload
            except urllib.error.HTTPError as error:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                retry_seconds = float(retry_after) if retry_after and retry_after.isdigit() else 0.0
                failure = error
            except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                retry_seconds = 0.0
                failure = error

            self.consecutive_failures += 1
            if self.consecutive_failures >= self.max_attempts:
                self.circuit_open = True
                break
            backoff = max(retry_seconds, min(60.0, 2.0 ** attempt))
            jitter = random.Random(hash(url) + attempt).uniform(0.0, 0.75)
            self.next_request_at = self.clock() + backoff + jitter

        raise RuntimeError("GDELT request failed and opened the circuit") from failure


def query_url(query: str, start: datetime, end: datetime) -> str:
    parameters = {
        "query": query,
        "mode": "artlist",
        "maxrecords": "250",
        "format": "json",
        "sort": "datedesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    return f"{GDELT_ENDPOINT}?{urllib.parse.urlencode(parameters)}"


def iter_windows(start: datetime, end: datetime, days: int) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + timedelta(days=days), end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def collect_historical(
    output: Path,
    cache_dir: Path,
    start: datetime,
    end: datetime,
    window_days: int = 14,
    client: GdeltClient | None = None,
    max_network_requests: int | None = None,
) -> dict[str, Any]:
    acquired_at = datetime.now(timezone.utc).isoformat()
    records = load_corpus(output)
    before = len(records)
    reports: list[dict[str, Any]] = []
    gdelt = client or GdeltClient(cache_dir)
    network_requests = 0

    for window_start, window_end in iter_windows(start, end, window_days):
        for query_id, query in DEFAULT_QUERIES.items():
            url = query_url(query, window_start, window_end)
            if (
                max_network_requests is not None
                and not gdelt.is_cached(url)
                and network_requests >= max_network_requests
            ):
                save_corpus(output, records)
                return _summary(output, before, records, reports, acquired_at, False)
            try:
                was_cached = gdelt.is_cached(url)
                payload = gdelt.get_json(url)
                if not was_cached:
                    network_requests += 1
                articles = payload.get("articles") or []
                added = 0
                for article in articles:
                    url = article.get("url")
                    title = article.get("title")
                    seen_date = article.get("seendate")
                    if not url or not title or not seen_date:
                        continue
                    published = gdelt_datetime(seen_date)
                    canonical_url = canonicalize_url(url)
                    identity = article_identity(canonical_url)
                    if identity in records:
                        continue
                    records[identity] = {
                        "article_id": identity,
                        "source_id": f"GDELT:{article.get('domain') or 'unknown'}",
                        "canonical_url": canonical_url,
                        "title": title.strip(),
                        "published_at_original": seen_date,
                        "published_at_utc": published.isoformat(),
                        "acquired_at_utc": acquired_at,
                        "language": (article.get("language") or "English").casefold(),
                        "content_hash": content_hash(title),
                        "collection_query": query_id,
                    }
                    added += 1
                reports.append(
                    {
                        "query_id": query_id,
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                        "status": "OK",
                        "fetched": len(articles),
                        "added": added,
                    }
                )
            except Exception as error:
                reports.append(
                    {
                        "query_id": query_id,
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                        "status": "ERROR",
                        "error": type(error).__name__,
                        "detail": str(error.__cause__ or error)[:200],
                    }
                )
                if gdelt.circuit_open:
                    save_corpus(output, records)
                    return _summary(output, before, records, reports, acquired_at, True)

    save_corpus(output, records)
    return _summary(output, before, records, reports, acquired_at, gdelt.circuit_open)


def _summary(
    output: Path,
    before: int,
    records: dict[str, dict[str, Any]],
    reports: list[dict[str, Any]],
    acquired_at: str,
    circuit_open: bool,
) -> dict[str, Any]:
    return {
        "acquired_at_utc": acquired_at,
        "corpus_path": output.as_posix(),
        "existing_records": before,
        "added_records": len(records) - before,
        "total_records": len(records),
        "requests": reports,
        "circuit_open": circuit_open,
        "llm_calls": 0,
        "llm_tokens": 0,
    }


def parse_utc_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect cached historical GDELT metadata")
    parser.add_argument("--output", type=Path, default=Path("data/raw/articles.jsonl"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/gdelt"))
    parser.add_argument("--start", required=True, type=parse_utc_date)
    parser.add_argument("--end", required=True, type=parse_utc_date)
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--max-network-requests", type=int)
    parser.add_argument("--summary", type=Path, default=Path("results/historical-collection.json"))
    args = parser.parse_args(argv)

    report = collect_historical(
        args.output,
        args.cache_dir,
        args.start,
        args.end,
        args.window_days,
        max_network_requests=args.max_network_requests,
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["added_records"] > 0 or report["total_records"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
