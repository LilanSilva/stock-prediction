# Contract-Freeze Backlog Overrides

## Status

This file is an authoritative interim overlay for every existing epic, story, and task under `backlog/`. The detailed task files were written before the requirements agreement and may contain stale queue names, schemas, producers, priors, or per-event behavior.

Do not implement a conflicting legacy statement. Authority is:

1. Executable shared message models when created.
2. [Canonical message contracts](../docs/contracts/message-contracts.md).
3. [Agreed system requirements](../docs/requirements/agreed-system-requirements.md).
4. Current functional documents and diagrams.
5. This override matrix.
6. Existing task detail.

After the contract freeze and POC-6 updates are propagated, affected tasks will be re-sliced and this interim overlay can be retired.

## Global overrides

- Use canonical asset and event IDs at all service boundaries.
- Use one PostgreSQL database with service-owned schemas, not one database per service.
- Publish domain events to durable topic exchange `feed.events` with canonical routing keys and independent consumer queues.
- Use the canonical envelope fields and payloads; task-local schema examples are non-authoritative.
- Use durable idempotency keys, outbox/reconciliation for state-plus-publish, DLQs, recovery, health/readiness, and graceful shutdown.
- Use local deterministic processing before LLM calls and record token metadata without additional calls.
- Formal `FR-*`, `NFR-*`, and `BR-*` traceability is removed.

## Conditional causality (condition-qualified causal edges + historical learning)

- `EventDetected` carries optional `polarity` (`OCCURRENCE`/`RESOLUTION`, default `OCCURRENCE`) and
  `context_tags` (list[ConditionCode], default `[]`). Additive, backward-compatible within major
  version 1.
- The causal graph stores the condition as a property on the `CAUSES` edge; each
  `(factor, asset, condition)` is a distinct edge. Unconditional edges omit `condition` and always
  fire. The blanket `MILITARY_CONFLICT -> BRENT_OIL` edge is superseded by conditioned edges
  (`TRANSPORT_AFFECTED` fires oil; `SAFE_HAVEN_ONLY` fires gold only).
- `ContributingEdge.edge_id` is `FACTOR->ASSET` (unconditional) or `FACTOR|CONDITION->ASSET`
  (conditioned). Credibility parses both forms.
- Prediction: conditions gate which edges fire; a `RESOLUTION` event inverts the edge sign; a
  `RESOLUTION`-driven DOWN on an asset is suppressed unless `RISK_PREMIUM_ELEVATED` is active
  (derived from Market Data `GET /prices/recent`). Still graph-only and zero prediction-time LLM
  calls (POC-6 `STOP` preserved).
- Credibility owns an offline, deterministic structure learner
  (`python -m credibility.learning.run`) that mines historical `EventDetected` payloads against
  realized price moves and upserts conditioned edges. It is a batch job, not an always-on service,
  and reads `cleansing.*`/`market_data.*` read-only as an offline-analytics exception.

## Epic overrides

### P06 POC-6 Validation Remediation

- P06 completed with `STOP` for prediction-time KG-plus-LLM arbitration.
- The controlled rerun used 30 reviewed conflict contexts and approved POC Yahoo reference-close policies for both assets.
- Every model attempt was counted against the single call/token/cost budget.
- Graph weights remained frozen during comparison and learning replay ran only after scoring.

### E01 Shared Infrastructure

- T02 creates one database with schemas `ingestion`, `cleansing`, `prediction`, `market_data`, `verification`, and `credibility`; enable `pgvector` where the Cleansing schema uses it.
- T04 declares `feed.events` as a durable topic exchange, all canonical work/live queues and bindings, `feed.dlx`, and one DLQ per durable work queue.
- Shared schemas implement the canonical contract verbatim before downstream service work.
- The RabbitMQ wrapper publishes by routing key and consumes an explicitly owned queue.
- The LLM wrapper is provider-configurable by environment settings and API key. It enforces compact input/output limits, cache identity, provider usage capture, and at most one malformed-output retry.

### E02 Ingestion

- Publish `ArticleIngested` with routing key `article.ingested`; do not publish directly to a legacy `raw-news` queue.
- Use the `ingestion` schema and transactional outbox/reconciliation.
- Raw HTML is not in the message and retention depends on the source policy.
- Apply SSRF, redirect, size, content-type, and timeout controls to body fetching.
- Treat GDELT as independently failing: honor `Retry-After`, use exponential backoff/jitter, cache successes, and open a circuit after repeated 429/transport failures. A fixed five-second sleep is not sufficient.

### E03 Cleansing

- Consume `cleansing.articles` and publish `event.detected`.
- Use the explicit cluster lifecycle and event-time quiet/watermark rules; article count never closes a cluster.
- Map Swedish/English actions locally to the canonical taxonomy before clustering Gate 2.
- Use an LLM only for ambiguous extraction or factual conflict, never merely for translation or every cluster.

### E04 Prediction

- Consume `prediction.events` into versioned per-asset multi-event contexts; do not make an isolated final decision per event.
- Graph-only decisions make zero LLM calls. Prediction-time LLM arbitration is deferred for M1 after the POC-6 `STOP` result.
- Publish only `PredictionMade` using `prediction.made`.
- Remove every requirement for Prediction to create or publish `PriceRequested`.
- Use `alpha=1.0`, `beta=1.0` for seeded learnable graph state.
- Stance management (news-driven, per asset): on a **trading day**, a new decision that matches the
  asset's latest active prediction on `(direction, magnitude)` produces nothing; a changed decision
  adds a new independent prediction (no supersede; each is scored). When the **market is closed or a
  price is unavailable** (weekend/holiday/fetch failure), the asset collapses to one active
  prediction: the new one sets `supersedes_prediction_id` to the prior stance, which is withdrawn
  and not scored. Replacement is always supersede, never in-place overwrite.

### E05 Market Data

- Consume `market-data.price-requests` and publish `price.observed`.
- Persist scheduled work before acknowledgement and rehydrate it after restart.
- One request contains both baseline and settlement sessions; one output contains both exact closes.
- Translate canonical IDs to provider symbols only in adapters.
- Yahoo daily bars are provider reference closes, not official settlements; preserve price kind, provider bar time, fetch time, adjustment flag, and registry version.
- `GC=F` and `BZ=F` are approved only as POC Yahoo reference-close series; Stooq remains unvalidated and disabled as fallback.

### E06 Verification

- Verification is the sole producer of `PriceRequested`.
- Resolve canonical baseline/settlement sessions without look-ahead and publish one dual-session request.
- Score only the dual-close `PriceObserved` using the fixed close-to-close/deadband/magnitude rules.
- Publish one idempotent `PredictionScored` and preserve both close observations.
- When a `PredictionMade` carries `supersedes_prediction_id` (a market-closed collapse), withdraw
  the superseded prediction's evaluation so it is never scored; a `PriceObserved` for a withdrawn
  evaluation is acknowledged without scoring.

### E07 Credibility

- Insert a processed-prediction ledger entry before any update; duplicate score delivery causes no learning.
- Initialize every Beta-Bernoulli state at `1.0/1.0`.
- Maintain edge directional validity, LLM arbiter quality, and predictive source utility separately.
- Do not label predictive source utility as factual truthfulness.
- Capture confidence now; defer advanced calibration reports until sample size is sufficient.

### E08 API Gateway

- Use read-only approved PostgreSQL views and read-only Neo4j access.
- Consume dedicated `gateway.predictions.live` and `gateway.scored.live` queues, never Verification/Credibility work queues.
- Keep business, scoring, and learning rules out of the Gateway.
- Bind locally for the POC; public authentication/rate limiting is deferred.

### E09 Dashboard

- Access data only through the Gateway.
- Display canonical IDs/labels, context provenance, both price sessions, decision method, and separated learning targets.
- LLM metadata display is observational and triggers no call.
- Handle WebSocket duplicates/reconnect with a REST refresh.

## Re-slicing gate

Do not estimate or implement a child task whose acceptance criteria conflict with this overlay. Re-slice after:

1. Shared executable contracts pass producer/consumer contract tests.
2. POC-6 `STOP` result is reflected in the relevant M1 task slices.
3. PostgreSQL and RabbitMQ topology decisions are frozen.
4. The core acceptance map is accepted.
