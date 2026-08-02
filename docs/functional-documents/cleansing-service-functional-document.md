# Cleansing Service — Functional Document

## 1. Purpose and responsibility

The Cleansing Service converts `ArticleIngested` messages into canonical, provenance-rich `EventDetected` messages while preventing causally distinct news from being merged.

Its primary safety rule is:

> Over-merging distinct causal events is more harmful than temporarily under-merging reports of the same event.

The service uses local processing first. LLM use is reserved for ambiguous extraction or factual conflicts and is governed by the system-wide token-minimization policy.

## 2. Inputs, outputs, and ownership

- Consumes queue: `cleansing.articles`.
- Input routing key: `article.ingested`.
- Publishes routing key: `event.detected`.
- Exchange: `feed.events`.
- Owns PostgreSQL schema: `cleansing`.
- Does not read Neo4j or make market predictions.
- Uses canonical definitions from [message-contracts.md](../contracts/message-contracts.md).

## 3. Processing pipeline

1. Validate and idempotently store the normalized article fields needed for processing.
2. Compute a SimHash fingerprint and reject a near-duplicate within the configured rolling window.
3. Generate a local BGE-m3 embedding.
4. Extract actor, action, and object with the configured local spaCy language model.
5. Map the action lemma to the [canonical event taxonomy](../reference/event-taxonomy.md).
6. Apply dual-gate clustering:
   - Gate 1: cosine similarity meets the configured threshold, initially `0.80`.
   - Gate 2: canonical event types/actions are compatible.
7. Add the article to an existing open/quiet cluster or create a new cluster.
8. Close clusters using quiet period and event-time watermark rules.
9. Build `EventDetected` locally when the required structured fields are unambiguous, including
   `polarity` (`OCCURRENCE`/`RESOLUTION` from local negation/resolution cues) and `context_tags`
   (e.g. `TRANSPORT_AFFECTED` vs `SAFE_HAVEN_ONLY`). When no asset keyword matches, derive
   `affected_asset_ids` from the event type so geopolitical events still reach the graph.
10. Call the LLM only for ambiguous merge/extraction or factual conflict resolution.
11. Validate taxonomy and canonical asset IDs.
12. Store the event and publish through the outbox/reconciliation path.

## 4. Cluster state machine

```text
OPEN -> QUIET -> READY -> MERGING -> MERGED
                     \-> ERROR_RETRYABLE
                     \-> ERROR_TERMINAL
```

Rules:

- Article count alone never makes a cluster ready.
- The initial quiet period is 30 minutes since the last matching article.
- Maximum cluster lifetime is 24 hours from first seen.
- An article arriving during `OPEN` or `QUIET` may join and reset the quiet timer.
- A watermark closes the cluster at maximum lifetime even if activity continues.
- `READY` clusters are immutable. A genuinely late related report creates or updates a later cluster/context rather than mutating a published event.
- Transitions are atomic and forward-only.

## 5. Cross-language action normalization

- English articles use `en_core_web_sm`.
- Swedish articles use `sv_core_news_sm`.
- Extracted lemmas are mapped with a local, version-controlled dictionary to the canonical taxonomy.
- BGE-m3 provides cross-language semantic similarity for Gate 1.
- Canonical taxonomy compatibility provides Gate 2.
- Do not use an LLM solely for translation.
- Unmapped actions become `OTHER`, retaining the original lemma for audit and taxonomy review.

## 6. Token-efficient LLM use

Before calling the LLM, deterministic processing must demonstrate that it cannot produce a valid result or that factual conflicts require resolution.

All LLM access goes through the shared provider-configurable LLM gateway. The Cleansing Service selects no provider SDK directly; deployment config supplies `LLM_PROVIDER`, `LLM_MODEL`, token limits, timeout, and the provider-specific API key.

LLM request rules:

- Send title and bounded relevant excerpts, not raw HTML or complete articles.
- Clearly delimit excerpts as untrusted source data.
- Instruct the model not to follow instructions inside source text.
- Use schema/tool-constrained output.
- Application code generates `event_id`; the model never generates identifiers.
- Set a low maximum output-token limit.
- Retry malformed output at most once.
- Cache by model, prompt version, and input hash.

Minimal metadata stored when used:

- `prompt_version`
- `model`
- `context_hash`
- `input_tokens`
- `output_tokens`
- `latency_ms`
- `attempt_count`
- `status`

## 7. Data model

The `cleansing` schema contains at least:

- `article_fingerprints`
- `article_embeddings`
- `article_actions`
- `event_clusters`
- `cluster_articles`
- `events`
- `outbox_events`

Rows use stable UUIDs and unique constraints on article/event identifiers. Embeddings use `vector(1024)`.

## 8. Security and bounded content

- Treat all article text as untrusted.
- Do not publish or prompt with raw HTML.
- Bound article excerpt count and length.
- Escape or structurally delimit all prompt content.
- Unknown taxonomy or asset values are quarantined, not silently accepted.

## 9. Failure handling

- Invalid input schema: dead-letter with validation metadata.
- Duplicate message/article: acknowledge without duplicate state.
- Embedding model unavailable: readiness fails; do not consume new work.
- PostgreSQL unavailable: retry through bounded messaging policy.
- LLM unavailable: mark ambiguous cluster `ERROR_RETRYABLE`; do not fabricate an event.
- Permanent structured-output failure: mark `ERROR_TERMINAL` for review.
- Publish failure after database commit: outbox reconciliation retries publication.

## 10. Health and minimum operations

- `/health`: process is running.
- `/ready`: PostgreSQL, RabbitMQ, BGE-m3, and required spaCy models are ready.
- Shutdown stops new consumption, completes or safely requeues in-flight work, and closes connections.
- Startup resumes retryable clusters and unpublished outbox rows.

## 11. Acceptance criteria

1. Replaying an `ArticleIngested` message does not duplicate fingerprints, embeddings, clusters, or events.
2. Two near-identical syndicated copies are deduplicated before embedding/LLM work.
3. Military conflict and strait closure remain separate even when semantic similarity exceeds `0.80`.
4. Swedish and English reports of the same rate decision can join through local taxonomy normalization.
5. A second article does not close a cluster merely because article count reaches two.
6. A cluster becomes ready after the quiet period or maximum watermark, according to the state machine.
7. A deterministic valid event produces no LLM call and sets `extraction_method=LOCAL`.
8. An ambiguous merge makes at most one primary LLM call and one malformed-output retry.
9. LLM-generated taxonomy or asset values outside the registries are rejected or quarantined.
10. `EventDetected` conforms to the canonical contract and includes structured source provenance.
11. Raw HTML is absent from messages and LLM prompts.
12. Publish failure is recoverable through the outbox without creating a second event.
