# E11: Bounded Growth / Retention for Unbounded Database Tables

Status: **Proposed, not scheduled.** Captured 2026-08-02; moved here from `new-feature/` on
2026-08-06 so unbuilt work lives in `backlog/`. No code exists for this yet — only
[Ingestion](../../requirements/SRS-02-ingestion.md#56-retention) has a retention cleaner today.

When this is built, the per-service rules become requirements in that service's SRS, not a copy of
this document.

## Motivation

Feed Analyzer uses one PostgreSQL database (`feed`) with six service-owned schemas. Every service
applies its own table DDL idempotently at startup. Auditing all tables shows that **only the
Ingestion Service has a scheduled cleanup job** (`RetentionCleaner`, see
[retention.py](../../src/services/ingestion/ingestion/retention.py) and
[config.py](../../src/services/ingestion/ingestion/config.py#L28-L33)). Every other schema accumulates
rows for the life of the deployment with no pruning, so the database grows without bound on a
continuously running POC.

This document lists the tables that grow continuously without any scheduled cleanup and proposes a
safe way to bound each one.

## Method

- Schema source of truth: each service's `db.py` (`SCHEMA_DDL`).
  - [ingestion/db.py](../../src/services/ingestion/ingestion/db.py)
  - [cleansing/db.py](../../src/services/cleansing/cleansing/db.py)
  - [prediction/db.py](../../src/services/prediction/prediction/db.py)
  - [market-data/db.py](../../src/services/market-data/market_data/db.py)
  - [verification/db.py](../../src/services/verification/verification/db.py)
  - [credibility/db.py](../../src/services/credibility/credibility/db.py)
- A table is flagged if new rows are appended over normal operation and no code deletes them.
- Tables that are **UPSERTED** (bounded row count) are not flagged.

## Summary: tables that grow without scheduled cleanup

| Schema | Table | Growth driver | Cleanup today | Category |
| --- | --- | --- | --- | --- |
| ingestion | `articles` | per ingested article | ✅ 30d retention | already bounded |
| ingestion | `outbox` | per published event | ✅ 7d delivered prune | already bounded |
| cleansing | `article_fingerprints` | per article (SimHash dedup) | ❌ none | intermediate |
| cleansing | `article_embeddings` | per article (`vector(1024)`) | ❌ none | intermediate (large) |
| cleansing | `article_actions` | per article | ❌ none | intermediate |
| cleansing | `event_clusters` | per detected cluster | ❌ none | intermediate |
| cleansing | `cluster_articles` | per clustered article | ❌ none | intermediate |
| cleansing | `events` | per merged event | ❌ none | business/intermediate |
| cleansing | `outbox_events` | per published event | ❌ none | outbox |
| prediction | `contexts` | per context window/version | ❌ none | intermediate |
| prediction | `context_events` | per event membership | ❌ none | intermediate |
| prediction | `predictions` | per prediction | ❌ none | business record |
| prediction | `contributing_edges` | per prediction edge | ❌ none | business record |
| prediction | `outbox_events` | per published event | ❌ none | outbox |
| market_data | `price_requests` | per price request | ❌ none | intermediate |
| market_data | `close_observations` | per unique close (cache) | ❌ none | reusable cache |
| market_data | `outbox` | per published event | ❌ none | outbox |
| verification | `evaluations` | per prediction tracked | ❌ none | business record |
| verification | `price_observations` | per observation | ❌ none | audit |
| verification | `scores` | per scored prediction | ❌ none | business record |
| verification | `outbox_events` | per published event | ❌ none | outbox |
| credibility | `credibility` | UPSERT per entity | n/a (bounded) | bounded |
| credibility | `credibility_history` | append-only per score | ❌ none | audit |
| credibility | `processed_predictions` | per applied prediction | ❌ none | idempotency guard |

**Not flagged:** `credibility.credibility` is upserted (one row per `(entity_id, entity_type)`), so
its row count is bounded by the number of graph edges plus news sources.

## Proposed cleanup by category

The existing Ingestion `RetentionCleaner` is the reference pattern: a config-gated, idempotent,
periodic `DELETE ... WHERE age > interval` loop run inside the service that owns the schema. Reuse it
per service rather than introducing a cross-schema janitor (a service must only write its own
schema).

### 1. Outbox tables (lowest risk, highest value) — prune delivered rows

`cleansing.outbox_events`, `prediction.outbox_events`, `market_data.outbox`,
`verification.outbox_events`.

All already carry `delivery_status` and `delivered_at`. A delivered row has served its purpose
(persist-before-publish reconciliation), so it is safe to delete after a short grace window,
exactly like `ingestion.outbox` already does:

```sql
DELETE FROM <schema>.<outbox_table>
WHERE delivery_status = 'DELIVERED'
  AND delivered_at < now() - make_interval(days => :outbox_retention_days);  -- e.g. 7
```

Keep `PENDING`/`FAILED` rows untouched so reconciliation and DLQ handling still work.

### 2. Cleansing intermediate tables — age out past the clustering/dedup window

`article_fingerprints`, `article_embeddings`, `article_actions`, `event_clusters`,
`cluster_articles`.

These exist only to de-duplicate and cluster **recent** news. Once a cluster is finalized and older
than the longest clustering deadline (plus the dedup lookback), the rows can never influence a new
event and are safe to drop. `article_embeddings` is the biggest space consumer (`vector(1024)` per
article), so it benefits most.

Suggested order (respect FKs — delete children first):

```sql
-- Finalized clusters older than the retention window.
DELETE FROM cleansing.cluster_articles
WHERE cluster_id IN (
    SELECT cluster_id FROM cleansing.event_clusters
    WHERE state = 'CLOSED' AND last_seen_at < now() - make_interval(days => :cluster_retention_days)
);
DELETE FROM cleansing.event_clusters
WHERE state = 'CLOSED' AND last_seen_at < now() - make_interval(days => :cluster_retention_days);

-- Per-article intermediates past the dedup lookback (keyed by published_at where available).
DELETE FROM cleansing.article_fingerprints
WHERE published_at < now() - make_interval(days => :dedup_retention_days);
DELETE FROM cleansing.article_embeddings  WHERE article_id NOT IN (SELECT article_id FROM cleansing.article_fingerprints);
DELETE FROM cleansing.article_actions     WHERE article_id NOT IN (SELECT article_id FROM cleansing.article_fingerprints);
```

Choose `:dedup_retention_days` >= the largest feed lookback and `:cluster_retention_days` >= the
longest cluster lifetime deadline so an in-flight cluster is never truncated.

### 3. `market_data.close_observations` — bounded reusable cache

This table is a price cache: rows are reused across replays (unique per
`(asset_id, session, registry_version)`), so growth is bounded by
`assets × sessions × registry_versions`. For a two-asset POC this is small and probably not worth
pruning.

> **Cross-schema dependency — do not prune below the learner window.** The offline structure learner
> ([credibility/learning/dataset.py](../../src/services/credibility/credibility/learning/dataset.py))
> reads `market_data.close_observations` (and `cleansing.events`) for the last `lookback_days` of
> history. Any retention on these two tables **must keep at least `lookback_days` of data**, or
> training samples silently disappear and Neo4j weight learning degrades (no crash, a correctness
> regression). Treat `cleansing.events` as a business/audit record (section 4), not an intermediate.

If asset coverage expands, cap by keeping only the most recent N sessions per asset (with
`N` >= the learner's `lookback_days` in sessions):

```sql
DELETE FROM market_data.close_observations o
USING (
    SELECT id, row_number() OVER (PARTITION BY asset_id ORDER BY session DESC) AS rn
    FROM market_data.close_observations
) ranked
WHERE o.id = ranked.id AND ranked.rn > :max_sessions_per_asset;  -- e.g. 400
```

`market_data.price_requests` can be pruned once its request reaches a terminal state and its
`updated_at` is older than the verification window.

### 4. Business / audit records — archive, do not silently delete

`prediction.predictions`, `prediction.contributing_edges`, `verification.evaluations`,
`verification.scores`, `verification.price_observations`, `credibility.credibility_history`,
`cleansing.events`.

These are the POC's actual output (accuracy history, learning audit trail) and should **not** be
deleted on a short timer. Options, in order of preference:

1. Keep for the POC (row counts are tiny at current volume) and revisit only if size becomes a
   problem.
2. Long-horizon retention (e.g. 180–365 days) with an explicit, documented policy.
3. Archive to a cold table / export before deleting, so accuracy and credibility trends survive.

Delete children before parents to respect the `ON DELETE CASCADE` / FK relationships
(`contributing_edges` → `predictions`, `context_events` → `contexts`).

### 5. `credibility.processed_predictions` — idempotency guard

Safe to prune a `prediction_id` only after its `PredictionScored` has been fully applied and no
redelivery can still arrive (past the broker's redelivery/DLQ window). Use a conservative age:

```sql
DELETE FROM credibility.processed_predictions
WHERE processed_at < now() - make_interval(days => :idempotency_retention_days);  -- e.g. 30
```

## Snapshot evidence retention

Include the additive snapshot and sampled-evaluation tables described in
[SRS-05](../../requirements/SRS-05-market-data.md#9-data-design) and
[SRS-06](../../requirements/SRS-06-verification.md#9-data-design). Preserve mapping versions, jobs and
sample evidence referenced by retained evaluations; never delete undelivered outbox rows or pending
jobs/evaluations. Account for at-least-once replay before pruning deduplication identities. Agree an
archive/export policy before enabling deletion. Current worker capacity limits pause reads instead
of silently discarding old evidence.

## Suggested retention implementation

- Add a `RetentionCleaner` (mirroring Ingestion) to each of Cleansing, Prediction, Market Data,
  Verification, and Credibility, scoped to that service's own schema.
- Config-gate each with `*_retention_enabled` and per-table `*_retention_days`, defaulting to
  conservative windows; expose them as `pydantic-settings` env vars.
- Run on the existing daily interval (`retention_interval_seconds`, default 86400) via the same
  background-task pattern as [ingestion/app.py](../../src/services/ingestion/ingestion/app.py).
- Make every delete idempotent and bounded; log deleted counts with structured logging, as the
  Ingestion cleaner already does.
- Never let a cleaner delete rows a downstream consumer still needs (open clusters, pending
  contexts, PENDING/FAILED outbox rows, unscored predictions).

## Out of scope / open questions

- Retention windows must be agreed against the documented POC retention policy
  ([SyRS §6.5 governance](../../requirements/SyRS-system.md#65-governance), `SYS-75`) and the
  Ingestion retention rules ([SRS-02 §5.6 and §10.5](../../requirements/SRS-02-ingestion.md#56-retention))
  before defaults are locked.
- Whether business/audit records are archived vs. deleted is a product decision, not a technical
  one.
- Neo4j (causal graph) is out of scope here; this document covers PostgreSQL tables only.
