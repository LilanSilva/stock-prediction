# Agreed System Requirements

## Status and authority

This document records the requirements agreed after the documentation audit. It supersedes conflicting statements in older functional documents, diagrams, POC notes, and backlog tasks.

Authority order:

1. Executable shared Pydantic message models.
2. [Canonical message contracts](../contracts/message-contracts.md).
3. This document and accepted architecture decisions.
4. Service functional documents.
5. Architecture diagrams.
6. Backlog tasks.

## Product outcome

The system must ingest news, preserve causally distinct events, combine concurrent events affecting the same asset, produce an explainable directional prediction, verify it using close-to-close market data, and update learned evidence without double-counting.

The POC does not execute trades or provide personalized financial advice.

## Multi-event prediction context

- Prediction must not process each event as an isolated final decision.
- Events are accumulated into an event-time context window and grouped by canonical asset ID.
- The initial configurable context window is 15 minutes.
- A context may contain multiple distinct `EventDetected` messages.
- The context records a version, window start/end, watermark, event IDs, and affected assets.
- Late events create a new context version. Published predictions remain immutable and may be superseded by a newer prediction.
- The prediction idempotency key is `(asset_id, context_window_start, horizon, context_version)`.
- One prediction is produced per affected asset and context version.
- Prediction-time LLM arbitration is deferred after the POC-6 `STOP` result. A future approved experiment may send all relevant distinct events and graph paths when forces genuinely conflict.

## Token-efficient LLM policy

- Local rules, spaCy, BGE-m3, deterministic taxonomy mapping, and graph traversal are used before any LLM call.
- The Cleansing Service calls the LLM only when deterministic merge/extraction cannot produce a valid event or when factual conflicts need resolution.
- The Prediction Service does not call the LLM for M1 graph-only prediction.
- Prediction-time conflict arbitration with the LLM is blocked unless a new controlled hypothesis is approved.
- Prompts contain compact structured event fields and relevant graph paths, not full articles.
- Configure a maximum input budget, a low maximum output-token limit, and concise rationales.
- Cache completed requests by prompt version, model, and context hash.
- Retry at most once for malformed structured output. Transient transport retries follow bounded backoff.
- Record model, prompt version, input/output token usage, latency, attempt count, status, and context hash. Recording metadata consumes no additional LLM tokens.
- Route all LLM calls through the shared LLM gateway.
- Select provider and model through configuration, not service code.
- API keys are provider-specific environment secrets, for example `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or approved cloud-provider credentials.
- The first M1 default provider/model is a deployment configuration choice; changing it must not change message contracts or service business logic.
- Operational dashboards and advanced fallback models are deferred.

## Canonical identifiers

- Business logic uses canonical `asset_id` values. Provider symbols never cross service boundaries as asset identities.
- Cleansing emits only event types from the versioned event taxonomy.
- Unknown assets or event types are quarantined with a reason; they are never silently treated as valid.
- The initial asset scope is `GOLD` and `BRENT_OIL`.

## Prediction and scoring

- The initial supported horizon is `ONE_TRADING_DAY`.
- `decision_at` is the time the immutable prediction becomes available.
- Verification is the sole owner of evaluation scheduling and `PriceRequested` publication.
- A single `PriceRequested` carries both `baseline_session` and `settlement_session`.
- A single `PriceObserved` returns the immutable baseline and settlement reference closes used for scoring.
- Sessions are resolved using the approved, versioned asset reference-series policy; canonical market identity and provider bar metadata are recorded separately.
- Provider daily closes must not be described as official exchange settlements.
- Continuous-futures assets require a pre-declared rollover policy before evaluation.
- Actual return is `(settlement_close - baseline_close) / baseline_close`.
- `abs(actual_return) < 0.003` is `NEUTRAL`; otherwise the sign determines `UP` or `DOWN`.
- `is_correct` is true only when predicted and actual directions match.
- Magnitude is `SMALL` below 1%, `MEDIUM` from 1% to below 3%, and `LARGE` at or above 3%. A deadband outcome is `SMALL`.
- Both price observations, price kind, source, session, provider bar time, fetch time, adjustment flag, and registry version are stored with the outcome.

## Data topology and ownership

- The POC uses one PostgreSQL instance and one database.
- Each service owns a schema and has write permission only to that schema.
- Schemas are `ingestion`, `cleansing`, `prediction`, `market_data`, `verification`, and `credibility`.
- The API Gateway has read-only access to approved tables/views.
- Neo4j stores the causal graph only.
- Database migrations are owned by the service that owns the schema.

## Messaging and reliability

- Services publish domain events to the durable topic exchange `feed.events`.
- Every consumer has its own queue; consumers never share another service's work queue for observation.
- Queue names, routing keys, bindings, retry rules, and DLQs are defined in the canonical contract.
- Scheduled market-data work is persisted before the input message is acknowledged.
- Pending work is rehydrated at startup.
- State-changing database writes and publications use an outbox or an equivalent reconciliation mechanism.
- Consumers use message IDs and domain idempotency keys to prevent duplicate state changes.
- Poison messages go to DLQ with failure metadata. Replay is an explicit operator action.

## Learning and credibility

- All Beta-Bernoulli state begins at `alpha=1.0`, `beta=1.0`.
- A processed-prediction ledger is mandatory before any update.
- Edge directional validity, arbiter decision quality, and source predictive utility are separate learned targets.
- An edge is evaluated against the observed direction represented by that edge, not automatically rewarded because the final prediction was correct.
- Source scoring is named `predictive_source_utility`; it must not be presented as factual truthfulness.
- Correlated predictions from the same asset and context window must not create duplicate independent evidence.
- Confidence is recorded from the first prediction. Brier score, calibration error, and reliability reports are computed only after sufficient history exists.

## Cleansing lifecycle

- Clusters use `OPEN -> QUIET -> READY -> MERGING -> MERGED`, with retryable and terminal error states.
- Article count alone never closes a cluster.
- A configurable quiet period closes an inactive cluster; the initial value is 30 minutes.
- The event-time watermark is capped by a 24-hour maximum cluster lifetime.
- Matching late articles before the watermark may join the open/quiet cluster.
- Swedish and English actions are mapped locally to the canonical event taxonomy before Gate 2 comparison.
- The LLM never generates event IDs; application code generates them.

## Minimum POC security boundary

- The POC API and Dashboard are local-only and must bind/configure exposure accordingly.
- The article fetcher permits only HTTP/HTTPS and blocks loopback, private, link-local, multicast, and metadata destinations.
- Every redirect target is revalidated.
- Response size, timeout, and content type are bounded.
- Article text is marked as untrusted data in LLM prompts.
- Production authentication, external CORS policy, and public rate limiting are deferred until external deployment is approved.

## Minimum operational correctness

- Each service exposes `/health` and `/ready`.
- Consumers stop accepting new messages on shutdown, finish or safely requeue in-flight work, and then close connections.
- Persistent pending work is recovered at startup.
- Structured logs include message ID, correlation ID, service, operation, and status.
- LLM token usage and latency are recorded from provider response metadata.
- Distributed tracing, load/soak testing, and full monitoring infrastructure are deferred until after the walking skeleton.

## Governance for the POC

- Record source terms and whether full article bodies/raw HTML may be retained.
- Default to storing the minimum text necessary for the POC.
- Document a retention period and financial-information disclaimer.
- Deletion automation, backup/restore automation, and long-term audit infrastructure are deferred until the POC graduates.

## Delivery gates

- P06 repaired the initial dataset and market-data gates and completed the controlled POC-6 rerun on 2026-07-13.
- The rerun recorded `STOP`: KG-plus-LLM had 2 paired corrections, 3 harms, and lower accuracy than graph-only on the 30-context sample.
- Build the M1 walking skeleton with graph-only prediction; do not implement prediction-time LLM arbitration unless a new controlled hypothesis is approved.
- Re-slice oversized tasks only after the contract freeze and topology changes are incorporated.
