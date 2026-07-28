# Ingestion Service Functional Document

## 1. Purpose

The Ingestion Service acquires permitted news content, normalizes it into a safe canonical article record, stores it idempotently, and publishes `ArticleIngested`. It does not classify events, predict markets, or score source utility.

## 2. Inputs and outputs

- Initial inputs: a small source set selected after P06 corpus collection. The four Swedish RSS endpoints passed the POC-6 availability canary and form the proven pool; the M1 walking skeleton starts with one of them and may add others. GDELT is optional until its 429/transport behavior is controlled.
- PostgreSQL owner: `ingestion` schema.
- Publishes routing key: `article.ingested` on `feed.events`.
- Consumer queue: `cleansing.articles` is owned by Cleansing, not Ingestion.
- Message shape: [ArticleIngested](../contracts/message-contracts.md#articleingested).

Raw HTML is never placed on RabbitMQ.

## 3. Polling and normalization

1. Run a configurable scheduled poll; the initial cadence may be hourly.
2. Parse source entries and canonicalize their URLs.
3. Fetch article bodies only where source terms permit it.
4. Normalize Unicode, whitespace, publication time, language, and country.
5. Compute a content hash.
6. Insert the article using a unique canonical URL/content rule.
7. Add an outbox record in the same transaction.
8. Publish the corresponding message and mark the outbox row delivered.

Duplicate polls do not create duplicate rows or messages with new business identities.

## 4. Fetcher security

- Permit only HTTP and HTTPS.
- Resolve and reject loopback, private, link-local, multicast, reserved, and cloud-metadata destinations.
- Re-resolve and revalidate every redirect target.
- Bound redirect count, response bytes, connection/read timeouts, and accepted content types.
- Do not forward local credentials, cookies, or authorization headers.
- Treat downloaded content as untrusted data.

## 5. Data model

Minimum `ingestion.articles` fields:

- `article_id` UUID primary key.
- `source_id`, canonical URL, title, normalized body.
- `published_at`, `language`, `country`.
- `content_hash`, ingestion timestamp.
- Optional bounded raw-content reference only when the source policy permits retention.

Minimum `ingestion.outbox` fields: message ID, aggregate ID, routing key, serialized payload, created time, delivery status, attempts, and last error.

## 6. Governance

The source registry records terms, attribution, permitted retention, and whether body extraction is allowed. Store the minimum article text required by the POC and apply the documented retention period. Do not add a source until these fields are recorded.

### 6.1 Retention and cleanup

The service enforces **time-based retention** to keep table growth bounded while preserving the
de-duplication guarantee. Because RSS and GDELT only surface *recent* items (RSS lists the last few
days; GDELT uses a bounded `timespan`), an article older than any feed's lookback window can never be
re-ingested, so it is safe to delete.

- A scheduled cleanup job runs on a fixed interval (default **daily**, `retention_interval_seconds`).
- `ingestion.articles` rows older than the retention window are deleted. The default window is
  **30 days** (`article_retention_days`), chosen to comfortably exceed the longest feed lookback plus
  a safety margin. The retention window must always be larger than the longest configured source
  lookback; otherwise a still-listed article could be re-ingested.
- `ingestion.outbox` rows with `delivery_status = 'DELIVERED'` older than `outbox_retention_days`
  (default **7 days**) are deleted; pending rows are never pruned.
- Cleanup is **decoupled** from downstream services: Cleansing consumes the `cleansing.articles`
  queue (not the table), so deletion is driven purely by row age, never by whether a consumer read it.
- Retention can be disabled with `retention_enabled=false`; all windows are environment-tunable.

## 7. Failure and recovery

- A failed source does not prevent other sources from completing.
- GDELT-specific throttling honors `Retry-After`, uses bounded exponential backoff/jitter and cached responses, and opens a circuit after repeated failures; fixed sleeps are insufficient.
- Transient fetch and broker failures use bounded retry with jitter.
- Permanent validation failures are recorded with a safe reason.
- Undelivered outbox rows are reconciled after restart.
- Messages are persistent and carry the canonical envelope identifiers.

## 8. Operations

- `/health` is process liveness.
- `/ready` checks PostgreSQL and RabbitMQ and reports source scheduler readiness.
- Shutdown stops new polls, completes or cancels active fetches safely, flushes eligible outbox work, and closes pools.
- Logs include source ID, article ID, message ID, correlation ID, operation, and status, but not full bodies or secrets.

## 9. Acceptance criteria

1. Each new permitted article creates one canonical row and one `ArticleIngested` identity.
2. Re-polling or restarting does not create a duplicate article.
3. Published messages match the canonical envelope and contain no raw HTML.
4. The service publishes through `feed.events` using `article.ingested`.
5. SSRF and response-boundary tests cover redirects and non-public destinations.
6. Outbox recovery publishes work left pending by a simulated crash.
7. Health, readiness, and graceful shutdown operate locally.
