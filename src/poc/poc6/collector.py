"""Resumable point-in-time RSS metadata collector for POC-6 remediation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from poc6 import RSS_SOURCES, _request, normalize_title, parse_rss


TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def canonicalize_url(value: str) -> str:
    """Normalize URL identity without changing publisher-owned path semantics."""
    parts = urllib.parse.urlsplit(value.strip())
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    query = urllib.parse.urlencode(
        sorted(
            (key, item)
            for key, item in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_PARAMETERS
        ),
        doseq=True,
    )
    return urllib.parse.urlunsplit((scheme, hostname, parts.path or "/", query, ""))


def article_identity(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def content_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def load_corpus(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                records[record["article_id"]] = record
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise ValueError(f"Invalid corpus record at {path}:{line_number}") from error
    return records


def save_corpus(path: Path, records: dict[str, dict[str, Any]]) -> None:
    """Atomically replace the replayable corpus after a successful collection cycle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    ordered = sorted(
        records.values(),
        key=lambda item: (item["published_at_utc"], item["article_id"]),
    )
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for record in ordered:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def collect(
    output: Path,
    fetcher: Callable[[str], bytes] = _request,
    acquired_at: datetime | None = None,
) -> dict[str, Any]:
    acquired = (acquired_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    records = load_corpus(output)
    existing_count = len(records)
    source_reports: list[dict[str, Any]] = []

    for source_id, url in RSS_SOURCES.items():
        try:
            articles = parse_rss(source_id, fetcher(url))
            added = 0
            for article in articles:
                canonical_url = canonicalize_url(article.url)
                identity = article_identity(canonical_url)
                if identity in records:
                    continue
                records[identity] = {
                    "article_id": identity,
                    "source_id": source_id,
                    "canonical_url": canonical_url,
                    "title": article.title,
                    "published_at_original": article.published_at_original,
                    "published_at_utc": article.published_at,
                    "acquired_at_utc": acquired,
                    "language": "sv",
                    "content_hash": content_hash(article.title),
                }
                added += 1
            source_reports.append(
                {"source_id": source_id, "status": "OK", "fetched": len(articles), "added": added}
            )
        except Exception as error:
            source_reports.append(
                {"source_id": source_id, "status": "ERROR", "error": type(error).__name__}
            )

    save_corpus(output, records)
    return {
        "acquired_at_utc": acquired,
        "corpus_path": output.as_posix(),
        "existing_records": existing_count,
        "added_records": len(records) - existing_count,
        "total_records": len(records),
        "source_reports": source_reports,
        "llm_calls": 0,
        "llm_tokens": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect replayable POC-6 RSS metadata")
    parser.add_argument("--output", type=Path, default=Path("data/raw/articles.jsonl"))
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)

    summary = collect(args.output)
    serialized = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if any(item["status"] == "OK" for item in summary["source_reports"]) else 1


if __name__ == "__main__":
    sys.exit(main())
