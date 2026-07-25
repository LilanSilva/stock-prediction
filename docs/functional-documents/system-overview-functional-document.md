# Feed Analyzer System Overview

## 1. Product purpose

Feed Analyzer is a local proof of concept that tests whether structured news events plus a causal knowledge graph can predict the next trading-session direction of selected commodities. It does not execute trades or provide personalized financial advice.

Initial scope:

- Assets: `GOLD` and `BRENT_OIL`.
- Horizon: `ONE_TRADING_DAY`.
- Outcome: `UP`, `DOWN`, or `NEUTRAL` with confidence and magnitude.
- Evaluation: immutable close-to-close observations.

The agreed requirements are authoritative in [agreed-system-requirements.md](../requirements/agreed-system-requirements.md); all service boundaries use [canonical message contracts](../contracts/message-contracts.md).

## 2. End-to-end behavior

1. Ingestion polls the small P06-approved source set, normalizes and stores new articles, then publishes `ArticleIngested`. GDELT remains optional until its failure behavior and corpus path pass.
2. Cleansing removes near-duplicates, maps multilingual actions locally, conservatively clusters related coverage, and publishes one provenance-rich `EventDetected` when a cluster is ready.
3. Prediction groups distinct events by canonical asset in a 60-minute event-time context window. Graph paths produce signed forces.
4. Prediction publishes graph-only results for M1. Genuine conflict LLM arbitration is deferred after the POC-6 `STOP` result unless a new controlled hypothesis is approved.
5. Verification persists the prediction's evaluation state, resolves its baseline and settlement sessions, and publishes one dual-session `PriceRequested`.
6. Market Data persists the request before acknowledgement, fetches both closes, and publishes one `PriceObserved`.
7. Verification calculates the close-to-close return and publishes one immutable `PredictionScored`.
8. Credibility idempotently updates edge validity, arbiter quality, and source predictive utility as separate learned targets.
9. The read-only Gateway and Dashboard expose current and historical results.

## 3. Components and ownership

| Component | Owns | Consumes | Publishes |
|---|---|---|---|
| Ingestion | Article acquisition and `ingestion` schema | External sources | `article.ingested` |
| Cleansing | Deduplication, clustering, taxonomy, `cleansing` schema | `cleansing.articles` | `event.detected` |
| Prediction | Context aggregation, graph inference, `prediction` schema | `prediction.events` | `prediction.made` |
| Verification | Evaluation schedule and scoring, `verification` schema | `verification.predictions`, `verification.prices` | `price.requested`, `prediction.scored` |
| Market Data | Provider translation and closes, `market_data` schema | `market-data.price-requests` | `price.observed` |
| Credibility | Duplicate-safe learning, `credibility` schema and graph weights | `credibility.scored` | none required for POC |
| Gateway | Read-only REST and live fan-out | dedicated Gateway live queues | WebSocket updates |
| Dashboard | Human-readable inspection | Gateway REST/WebSocket | none |

## 4. Data architecture

- One PostgreSQL instance and one database.
- Service-owned schemas: `ingestion`, `cleansing`, `prediction`, `market_data`, `verification`, `credibility`.
- A service writes only its own schema. Cross-service reads use approved views or messages.
- The Gateway receives read-only grants.
- Neo4j stores only the causal graph and learned edge state.
- Provider symbols are adapter details; all business boundaries use canonical asset IDs.

## 5. Messaging architecture

All domain events are published persistently to the durable topic exchange `feed.events`. Each consumer has an independent queue, and every durable work queue has a DLQ. State-changing publish operations use an outbox or equivalent reconciliation process.

The exact exchange bindings, envelopes, enums, and payloads are defined only in [message-contracts.md](../contracts/message-contracts.md).

## 6. Multi-event prediction

- Context key: asset, event-time window, horizon, and version.
- Initial window: 60 minutes.
- Late relevant events create a new immutable context version and may supersede an earlier prediction.
- Idempotency key: `(asset_id, context_window_start, horizon, context_version)`.
- Distinct causal events remain distinct; context aggregation does not merge them into one event.
- Only graph paths relevant to the affected asset enter prediction.

## 7. Token-minimization policy

The system uses local deterministic processing first. Cleansing calls an LLM only when valid structured extraction or factual conflict resolution cannot be completed locally. Prediction does not call an LLM in M1.

Any future approved prediction-arbitration experiment permits at most one primary call per eligible asset/context version, plus one malformed-output retry. Inputs contain compact structured fields and relevant graph paths, never full articles. Results are cached by model, prompt version, and context hash. Model, input/output tokens, latency, attempts, and status are recorded from provider metadata without extra calls.

## 8. Scoring and learning

Actual return is `(settlement_close - baseline_close) / baseline_close`.

- Absolute return below `0.003`: `NEUTRAL`.
- Otherwise positive: `UP`; negative: `DOWN`.
- Magnitude: `SMALL` below 1%, `MEDIUM` below 3%, otherwise `LARGE`.
- Correctness requires predicted direction to equal actual direction.

All Beta-Bernoulli learning starts at `alpha=1.0`, `beta=1.0`. A processed-prediction ledger prevents duplicate evidence. Confidence is captured immediately; advanced calibration reports wait for sufficient history.

## 9. Minimum security and operations

- API and Dashboard bind locally for the POC.
- Article fetching allows only HTTP/HTTPS, validates every redirect, blocks non-public destinations, and bounds size, type, and timeout.
- Article content is untrusted prompt data.
- Every service provides liveness and readiness endpoints.
- Consumers drain or safely requeue in-flight work during shutdown.
- Persistent scheduled work is recovered after restart.
- Structured logs include message and correlation IDs, operation, and status; secrets and full prompts/articles are excluded.

## 10. Delivery sequence

1. Freeze canonical contracts, registries, topology, and scoring rules.
2. Apply the completed POC-6 `STOP` result by keeping prediction-time LLM arbitration out of M1.
3. Build the narrow graph-only GOLD/BRENT walking skeleton.
4. Keep future LLM arbitration behind a new controlled hypothesis.
5. Add minimum recovery and security correctness.
6. Build the read-only Gateway and Dashboard.
7. Expand assets and sources only after evidence supports it.

Formal `FR-*`, `NFR-*`, and `BR-*` traceability is intentionally excluded. Lightweight ADRs, service acceptance criteria, and the [core acceptance map](../requirements/core-acceptance-map.md) provide sufficient POC governance.
