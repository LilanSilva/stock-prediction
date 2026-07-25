# T01: Point-in-Time Context Collector

**Status:** COMPLETE

**Evidence:** [historical_collector.py](../../../../src/poc/poc6/historical_collector.py), [historical collector tests](../../../../src/poc/poc6/test_historical_collector.py), [raw article corpus](../../../../src/poc/poc6/data/raw/articles.jsonl), and [collection summary](../../../../src/poc/poc6/results/historical-collection.json).

## Purpose

Collect enough post-August-2025 news metadata to construct multi-event contexts without selecting stories based on known price outcomes.

## Requirements

- Start with the four validated RSS feeds; treat GDELT as optional and independently failing.
- For GDELT, honor `Retry-After`, use exponential backoff with jitter, cache successful pages, and open a circuit after repeated 429/transport failures.
- Store source ID, canonical URL, title, source publication time, acquisition time, language, and content hash.
- Store the minimum permitted text; do not require full article bodies for POC-6.
- Normalize times to UTC while retaining the original timestamp.
- Make collection resumable and duplicate-safe.
- Do not read or join market outcomes in this task.

## Completion notes

P06 used the historical GDELT collector to build a reproducible corpus of 4,586 unique article metadata rows. The corpus was collected before outcome prices were joined and is replayable from local fixtures.

## Acceptance criteria

1. A restart does not duplicate an article identity.
2. One unavailable source does not fail the collection cycle.
3. GDELT backoff performs no fixed unconditional five-second loop.
4. The frozen raw corpus can be replayed without network access.
5. Source terms and retained fields are documented.
