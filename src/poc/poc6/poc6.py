"""Zero-dependency POC-6 gate runner.

The runner deliberately performs data and market-quality gates before any paid
model invocation.  It stores only the minimum article evidence needed to audit
selected contexts: title, URL, source, and publication time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable


USER_AGENT = "feed-analyzer-poc6/1.0 (local research POC)"

RSS_SOURCES = {
    "DI": "https://www.di.se/rss",
    "DN": "https://www.dn.se/rss/",
    "SVD": "https://www.svd.se/feed/articles.rss",
    "AFTONBLADET": "https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/",
}

PRICE_SERIES = {
    "GOLD": {
        "symbol": "GC=F",
        "expected_calendar": "COMEX",
        "expected_timezone": "America/New_York",
    },
    "BRENT_OIL": {
        "symbol": "BZ=F",
        "expected_calendar": "ICE_EUROPE",
        "expected_timezone": "Europe/London",
    },
}


@dataclass(frozen=True)
class SignalRule:
    name: str
    event_type: str
    patterns: tuple[str, ...]
    forces: dict[str, int]


SIGNAL_RULES = (
    SignalRule(
        "military_conflict",
        "MILITARY_CONFLICT",
        (
            r"\bwar\b",
            r"\battack(?:s|ed)?\b",
            r"\bstrike(?:s|d)?\b",
            r"\binvasion\b",
            r"\bkrig\b",
            r"\battacker(?:ar|ade)?\b",
            r"\banfall\b",
            r"\binvasion\b",
        ),
        {"GOLD": 1, "BRENT_OIL": 1},
    ),
    SignalRule(
        "strait_closure",
        "STRAIT_CLOSURE",
        (
            r"strait.{0,30}(?:clos|block)",
            r"(?:clos|block).{0,30}strait",
            r"sund.{0,30}(?:stäng|block)",
            r"(?:stäng|block).{0,30}sund",
            r"hormuz.{0,30}(?:clos|block|stäng)",
        ),
        {"GOLD": -1, "BRENT_OIL": 1},
    ),
    SignalRule(
        "supply_disruption",
        "SUPPLY_DISRUPTION",
        (
            r"(?:oil|gas|pipeline|production).{0,35}(?:halt|cut|disrupt|damage|stop)",
            r"(?:halt|cut|disrupt|damage|stop).{0,35}(?:oil|gas|pipeline|production)",
            r"(?:olja|gas|ledning|produktion).{0,35}(?:stopp|avbrott|skad|minsk)",
        ),
        {"BRENT_OIL": 1},
    ),
    SignalRule(
        "sanctions",
        "SANCTIONS",
        (r"\bsanction(?:s|ed)?\b", r"\bsanktion(?:er|erna)?\b", r"\bembargo\b"),
        {"GOLD": 1, "BRENT_OIL": 1},
    ),
    SignalRule(
        "rate_hike",
        "RATE_DECISION",
        (
            r"(?:rate|rates).{0,25}(?:hike|raise|raised|increase)",
            r"(?:ränta|räntan).{0,25}(?:höj|höjs|höjde)",
        ),
        {"GOLD": -1},
    ),
    SignalRule(
        "rate_cut",
        "RATE_DECISION",
        (
            r"(?:rate|rates).{0,25}(?:cut|lower|lowered|decrease)",
            r"(?:ränta|räntan).{0,25}(?:sänk|sänks|sänkte)",
        ),
        {"GOLD": 1},
    ),
    SignalRule(
        "inflation_rise",
        "INFLATION_CHANGE",
        (
            r"inflation.{0,25}(?:rise|rises|rose|higher|accelerat)",
            r"inflation.{0,25}(?:stiger|ök|högre)",
        ),
        {"GOLD": 1},
    ),
    SignalRule(
        "inflation_fall",
        "INFLATION_CHANGE",
        (
            r"inflation.{0,25}(?:fall|falls|fell|lower|slow)",
            r"inflation.{0,25}(?:faller|sjunk|lägre|broms)",
        ),
        {"GOLD": -1},
    ),
    SignalRule(
        "recession_signal",
        "RECESSION_SIGNAL",
        (r"\brecession\b", r"\bcontraction\b", r"\brecession(?:en)?\b", r"\blågkonjunktur\b"),
        {"GOLD": 1, "BRENT_OIL": -1},
    ),
)


@dataclass(frozen=True)
class Article:
    source_id: str
    title: str
    url: str
    published_at: str
    published_at_original: str | None = None


@dataclass(frozen=True)
class EventSignal:
    event_id: str
    source_id: str
    title: str
    url: str
    published_at: str
    rule: str
    event_type: str
    asset_id: str
    direction: str


class BudgetExceeded(RuntimeError):
    pass


class BudgetLedger:
    """Counts all attempts, including malformed-output retries."""

    def __init__(
        self,
        max_calls: int = 40,
        max_input_tokens: int = 80_000,
        max_output_tokens: int = 8_000,
    ) -> None:
        self.max_calls = max_calls
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def reserve(self, input_tokens: int, output_tokens: int) -> None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative")
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded("call budget exceeded")
        if self.input_tokens + input_tokens > self.max_input_tokens:
            raise BudgetExceeded("input-token budget exceeded")
        if self.output_tokens + output_tokens > self.max_output_tokens:
            raise BudgetExceeded("output-token budget exceeded")
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    @property
    def estimated_usd(self) -> float:
        return round(self.input_tokens / 1_000_000 * 3 + self.output_tokens / 1_000_000 * 15, 6)


def _request(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_rss(source_id: str, content: bytes) -> list[Article]:
    root = ET.fromstring(content)
    articles: list[Article] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        published_original = item.findtext("pubDate") or item.findtext("date")
        published = _parse_date(published_original)
        if not title or not url or published is None:
            continue
        articles.append(
            Article(
                source_id=source_id,
                title=title,
                url=url,
                published_at=published.isoformat(),
                published_at_original=published_original,
            )
        )
    return articles


def normalize_title(title: str) -> str:
    return " ".join(re.findall(r"[a-zåäö0-9]+", title.casefold()))


def deduplicate_articles(articles: Iterable[Article]) -> list[Article]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique: list[Article] = []
    for article in sorted(articles, key=lambda value: value.published_at):
        normalized = normalize_title(article.title)
        if article.url in seen_urls or normalized in seen_titles:
            continue
        seen_urls.add(article.url)
        seen_titles.add(normalized)
        unique.append(article)
    return unique


def detect_signals(article: Article) -> list[EventSignal]:
    normalized = normalize_title(article.title)
    signals: list[EventSignal] = []
    for rule in SIGNAL_RULES:
        if not any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in rule.patterns):
            continue
        for asset_id, sign in rule.forces.items():
            identity = f"{article.url}|{rule.name}|{asset_id}"
            signals.append(
                EventSignal(
                    event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    source_id=article.source_id,
                    title=article.title,
                    url=article.url,
                    published_at=article.published_at,
                    rule=rule.name,
                    event_type=rule.event_type,
                    asset_id=asset_id,
                    direction="UP" if sign > 0 else "DOWN",
                )
            )
    return signals


def context_window_start(value: str) -> str:
    parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
    return parsed.replace(minute=0, second=0, microsecond=0).isoformat()


def build_conflict_contexts(signals: Iterable[EventSignal]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[EventSignal]] = {}
    for signal in signals:
        key = (signal.asset_id, context_window_start(signal.published_at))
        grouped.setdefault(key, []).append(signal)

    contexts: list[dict[str, Any]] = []
    for (asset_id, window_start), items in sorted(grouped.items()):
        directions = {item.direction for item in items}
        article_urls = {item.url for item in items}
        if directions != {"UP", "DOWN"} or len(article_urls) < 2:
            continue
        context_id = hashlib.sha256(f"{asset_id}|{window_start}".encode("utf-8")).hexdigest()[:24]
        contexts.append(
            {
                "context_id": context_id,
                "asset_id": asset_id,
                "window_start": window_start,
                "signals": [asdict(item) for item in items],
            }
        )
    return contexts


def fetch_sources() -> dict[str, Any]:
    source_reports: list[dict[str, Any]] = []
    all_articles: list[Article] = []
    for source_id, url in RSS_SOURCES.items():
        try:
            articles = parse_rss(source_id, _request(url))
            source_reports.append({"source_id": source_id, "status": "OK", "articles": len(articles)})
            all_articles.extend(articles)
        except Exception as error:  # POC report must retain per-source failure
            source_reports.append(
                {"source_id": source_id, "status": "ERROR", "error": type(error).__name__}
            )

    unique_articles = deduplicate_articles(all_articles)
    signals = [signal for article in unique_articles for signal in detect_signals(article)]
    contexts = build_conflict_contexts(signals)
    return {
        "sources": source_reports,
        "article_count": len(all_articles),
        "unique_article_count": len(unique_articles),
        "signal_count": len(signals),
        "conflict_context_count": len(contexts),
        "minimum_required_contexts": 30,
        "gate_passed": len(contexts) >= 30,
        "contexts": contexts,
    }


def _yahoo_chart(symbol: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d&events=history"
    payload = json.loads(_request(url).decode("utf-8"))
    return payload["chart"]["result"][0]


def _stooq_canary(symbol: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://stooq.com/q/d/l/?s={encoded}&d1=20260101&d2=20261231&i=d"
    try:
        text = _request(url).decode("utf-8", errors="replace").strip()
    except Exception as error:
        return {"symbol": symbol, "status": "ERROR", "error": type(error).__name__}
    valid_csv = text.startswith("Date,") and len(text.splitlines()) > 1
    return {
        "symbol": symbol,
        "status": "OK" if valid_csv else "INVALID_RESPONSE",
        "valid_csv": valid_csv,
    }


def fetch_prices() -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for asset_id, config in PRICE_SERIES.items():
        try:
            chart = _yahoo_chart(config["symbol"])
            metadata = chart["meta"]
            timestamps = chart.get("timestamp") or []
            closes = (chart.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            observations = [
                {
                    "session_label_utc": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                    "close": close,
                }
                for timestamp, close in zip(timestamps, closes)
                if close is not None and math.isfinite(float(close)) and float(close) > 0
            ]
            timezone_matches = metadata.get("exchangeTimezoneName") == config["expected_timezone"]
            reports.append(
                {
                    "asset_id": asset_id,
                    "symbol": config["symbol"],
                    "status": "OK",
                    "instrument_type": metadata.get("instrumentType"),
                    "currency": metadata.get("currency"),
                    "provider_exchange": metadata.get("exchangeName"),
                    "provider_timezone": metadata.get("exchangeTimezoneName"),
                    "expected_calendar": config["expected_calendar"],
                    "expected_timezone": config["expected_timezone"],
                    "timezone_matches": timezone_matches,
                    "observation_count": len(observations),
                    "first_observation": observations[0] if observations else None,
                    "last_observation": observations[-1] if observations else None,
                }
            )
        except Exception as error:
            reports.append(
                {
                    "asset_id": asset_id,
                    "symbol": config["symbol"],
                    "status": "ERROR",
                    "error": type(error).__name__,
                }
            )

    stooq = [_stooq_canary("^gold"), _stooq_canary("^oil")]
    yahoo_available = all(
        report.get("status") == "OK" and report.get("observation_count", 0) >= 10
        for report in reports
    )
    calendar_consistent = all(report.get("timezone_matches") is True for report in reports)
    stooq_available = all(report.get("valid_csv") is True for report in stooq)
    roll_policy_defined = False
    gate_passed = yahoo_available and calendar_consistent and roll_policy_defined
    return {
        "yahoo": reports,
        "stooq": stooq,
        "yahoo_available": yahoo_available,
        "calendar_consistent": calendar_consistent,
        "stooq_fallback_validated": stooq_available,
        "roll_policy_defined": roll_policy_defined,
        "gate_passed": gate_passed,
        "notes": [
            "Yahoo daily timestamps are provider session labels, not official settlement observation times.",
            "The POC requires a pre-declared continuous-futures rollover policy.",
        ],
    }


def run() -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    source_gate = fetch_sources()
    price_gate = fetch_prices()
    prerequisites_passed = source_gate["gate_passed"] and price_gate["gate_passed"]
    if prerequisites_passed:
        decision = "READY_FOR_CAPPED_LLM_EXPERIMENT"
        reason = "Source/context and market-data gates passed."
    else:
        decision = "REVISE"
        reason = (
            "POC-6 early-stop rule applied before paid inference: a reproducible conflict dataset "
            "and/or auditable market-data policy is not yet available."
        )
    return {
        "poc": "POC-6",
        "run_started_at": started_at.isoformat(),
        "run_finished_at": datetime.now(timezone.utc).isoformat(),
        "llm_calls": 0,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "estimated_llm_cost_usd": 0.0,
        "source_gate": source_gate,
        "price_gate": price_gate,
        "decision": decision,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the zero-token POC-6 prerequisite gates")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    args = parser.parse_args(argv)

    report = run()
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["decision"] != "ERROR" else 1


if __name__ == "__main__":
    sys.exit(main())
