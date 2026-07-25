# T01: SimHash near-duplicate detector

## Context

This task implements the first gate in the Cleansing Service's deduplication pipeline. It lives in `services/cleansing/` and is the very first processing step when an `ArticleIngested` message arrives from the `raw-news` queue. Before spending CPU on spaCy NLP (T02) or GPU on BGE-m3 embedding (S02/T01), we cheaply reject wire-syndication duplicates using SimHash fingerprinting.

The problem this solves: a single Reuters or AP story is often republished verbatim (or near-verbatim) by dozens of outlets within minutes. Without dedup, each copy would be embedded and potentially merged into the same cluster, inflating `source_count` with fake diversity and corrupting the LLM merge prompt with redundant text.

## Background

**SimHash** produces a fixed-width bit-string fingerprint of text such that two near-identical texts have a low Hamming distance between their fingerprints. A Hamming distance <= 3 means the texts share the overwhelming majority of bit positions — a reliable signal for near-duplicate content.

We use the `simhash` Python library (not `datasketch` MinHash — SimHash is better suited for Hamming-distance dedup). The fingerprint input is the normalized concatenation of:
- Article `title` (full)
- First 200 characters of article `body`

This short window is intentional: wire copies differ most in body length (one outlet may add editorial context), but title and opening are near-identical. Using only the first 200 chars makes the fingerprint robust to tail-end differences.

**Storage:** Fingerprints are stored in a Postgres table `article_fingerprints` in the `events` database. On each incoming article, we query for any fingerprint within Hamming distance <= 3 created in the last 24 hours. If found, the article is a duplicate and is silently dropped (message ACKed, nothing written, no embedding triggered).

**24-hour rolling window:** Old news is not a duplicate of current news. A story about "Iran closes strait" from 6 months ago should not suppress a new story with the same headline. The 24h window prevents false positives across unrelated news cycles.

**Queue and message:** Consumes `ArticleIngested` from `raw-news` queue using `aio-pika`.

## Inputs

- **RabbitMQ message:** `ArticleIngested` from queue `raw-news`
  - Fields used: `article_id` (str), `title` (str), `body` (str), `published_at` (datetime), `correlation_id` (str)
  - Full schema defined in `src/shared/schemas/messages.py` as `ArticleIngested` Pydantic model
- **Postgres table `article_fingerprints`** (read): existing fingerprints within the last 24h window

## Outputs

- **Postgres table `article_fingerprints`** (write): one new row per non-duplicate article
- **Side effect (pass-through):** non-duplicate articles are forwarded to the next processing step (spaCy action signature extraction, T02)
- **Side effect (drop):** duplicate articles are ACKed on the queue and processing stops — nothing is written for duplicates

## Technical Requirements

### Python Libraries
- `simhash==2.1.2` (or latest compatible) — `from simhash import Simhash`
- `asyncpg` or `psycopg[async]` — async Postgres driver (use whichever the shared library already uses)
- `aio-pika` — RabbitMQ consumer (via shared library wrapper)
- `pydantic` — message schema validation

### Postgres Table Schema

Create table `article_fingerprints` in the `events` Postgres database:

```sql
CREATE TABLE IF NOT EXISTS article_fingerprints (
    id          BIGSERIAL PRIMARY KEY,
    article_id  TEXT        NOT NULL UNIQUE,
    fingerprint BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_fingerprints_created_at ON article_fingerprints (created_at);
```

Note: `fingerprint` is stored as `BIGINT` (64-bit integer) — the raw integer output of `Simhash(text).value`.

### Fingerprint Computation

```python
from simhash import Simhash

def compute_fingerprint(title: str, body: str) -> int:
    text = f"{title} {body[:200]}"
    # Normalize: lowercase, collapse whitespace
    text = " ".join(text.lower().split())
    return Simhash(text).value
```

### Duplicate Detection Query

Postgres does not natively support Hamming distance on integers. Perform the Hamming distance check in Python:

1. Query `article_fingerprints` for all rows where `created_at >= NOW() - INTERVAL '24 hours'`
2. For each returned fingerprint, compute `bin(fp_existing XOR fp_new).count('1')` (popcount = Hamming distance)
3. If any distance <= 3, the article is a duplicate

For scale, limit the 24h window query to at most 10,000 rows (add `LIMIT 10000`). In normal operation (hourly ingestion of ~50 articles) this is well under limit.

### File Layout

```
services/cleansing/
  dedup/
    __init__.py
    fingerprint.py       # compute_fingerprint(), is_duplicate(), store_fingerprint()
  db/
    migrations/
      001_create_fingerprints.sql
```

### Message ACK Strategy

- ACK the message **only after** `is_duplicate()` check completes and, if not duplicate, after `store_fingerprint()` succeeds
- If Postgres write fails, do NOT ACK — let aio-pika requeue the message (nack with requeue=True)
- If the article is a duplicate, ACK immediately — do not requeue

### Idempotency

The `article_fingerprints` table has a `UNIQUE` constraint on `article_id`. If the same `article_id` is processed twice (e.g., after a consumer restart), the `INSERT` uses `ON CONFLICT (article_id) DO NOTHING` — preventing duplicate rows while allowing the processing to continue (the article should still be checked as a potential duplicate of other articles).

## Acceptance Criteria

1. Given two `ArticleIngested` messages with identical titles and near-identical bodies (e.g., body differs only in trailing sentence), the second message is dropped: no new row in `article_fingerprints` for the second `article_id`, and no embedding or LLM call is triggered.
2. Given two `ArticleIngested` messages about completely different topics, both are stored in `article_fingerprints` and both proceed to the next processing step.
3. Given an article published more than 24 hours ago in the fingerprint table with Hamming distance <= 3, a new article is NOT treated as a duplicate (24h window is respected).
4. `compute_fingerprint()` returns a consistent `int` for the same input — calling it twice on the same text yields the same value.
5. Hamming distance computation is correct: `hamming(fp, fp) == 0`, `hamming(fp, fp ^ 1) == 1`, `hamming(fp, ~fp & 0xFFFFFFFFFFFFFFFF) == 64`.
6. If Postgres is unavailable, the message is nacked and requeued (not dropped silently).
7. The `article_fingerprints` table has a unique index on `article_id`; inserting the same `article_id` twice does not raise an unhandled exception.
8. Unit tests in `services/cleansing/tests/test_fingerprint.py` cover: fingerprint computation, Hamming distance calculation, duplicate detection with mocked Postgres, and the 24h window boundary.

## Implementation Notes

- **Performance:** The 24h window query can return hundreds of rows. Hamming distance computation in Python is O(n) in the number of existing fingerprints. At typical ingestion rates (50-200 articles/hour), the 24h window will have at most ~5,000 rows — Python popcount on 5,000 ints is sub-millisecond. No optimization needed at this scale.
- **Simhash library choice:** Use the `simhash` package (pure Python, well-tested). The `datasketch` package provides MinHash (Jaccard similarity), which is less suited to near-duplicate detection than SimHash (Hamming similarity).
- **Swedish articles:** SimHash is language-agnostic — it operates on character n-grams or tokens. Default tokenization (split on whitespace) works fine for Swedish. No special handling needed.
- **Encoding normalization:** The `body` field in `ArticleIngested` is already Unicode-normalized by the Ingestion Service. No re-normalization needed here.
- **XOR popcount in Python:** `bin(a ^ b).count('1')` is idiomatic Python. For very large batches, `gmpy2.popcount()` is faster but adds a C dependency — avoid unless profiling shows a bottleneck.
- **Table migration:** Create the table via a SQL migration file in `services/cleansing/db/migrations/001_create_fingerprints.sql`. Run migrations at service startup using a simple `asyncpg.execute()` call before starting the consumer.

## Definition of Done

- [ ] Unit tests pass (`pytest services/cleansing/tests/test_fingerprint.py`)
- [ ] Code passes `ruff check services/cleansing/` with zero errors
- [ ] Code passes `mypy services/cleansing/dedup/fingerprint.py` with no type errors
- [ ] `article_fingerprints` table is created by migration on first service startup
- [ ] Duplicate articles are silently dropped (ACKed, not requeued) without writing to any table
- [ ] Non-duplicate articles write exactly one row to `article_fingerprints` and proceed to next step
- [ ] Idempotent insert (`ON CONFLICT DO NOTHING`) is implemented and tested
- [ ] 24-hour rolling window is enforced in the duplicate-check query
- [ ] Service startup log confirms "Deduplication pipeline initialized" with fingerprint table row count
