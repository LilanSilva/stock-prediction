# Market Data Service Functional Document

## 1. Purpose

The Market Data Service fulfills a persisted dual-session price request and returns the exact approved provider reference closes used for scoring. It does not select horizons, evaluate predictions, claim unofficial bars are exchange settlements, or expose provider symbols as business identities.

## 2. Inputs, outputs, and ownership

- Consumes `market-data.price-requests`, bound to `price.requested`.
- Publishes `price.observed` on `feed.events`.
- Owns PostgreSQL schema `market_data`.
- Uses the [asset registry](../reference/asset-registry.md) to translate canonical IDs inside adapters.
- Implements the canonical `PriceRequested` and `PriceObserved` contracts.

## 3. Request lifecycle

1. Validate the envelope, asset ID, calendar, baseline session, and settlement session.
2. Derive a deterministic work identity from `request_id`.
3. Persist request and pending schedule before acknowledging RabbitMQ.
4. Fetch any already-available baseline close and persist it immutably.
5. At or after the settlement session is complete, fetch the approved settlement-session reference close.
6. Publish one `PriceObserved` containing both stored observations.
7. Mark the request completed only after publication is durably recorded/reconciled.

On startup, reload all pending or publish-pending requests. In-memory scheduling alone is insufficient.

## 4. Adapter rules

- Accept canonical `asset_id`, not a provider symbol.
- Resolve provider symbol, timezone, currency, and calendar from the registry.
- Validate the returned session date, currency, instrument, and positive finite close.
- Preserve provider source/symbol, session, provider bar time, fetch time, price kind, adjustment flag, and registry version.
- Apply the registry's pre-declared continuous-futures rollover policy before accepting an observation.
- A fallback may be used only when the registry maps an economically equivalent instrument. No silent substitution is allowed.

P06/T03 approved a POC-only Yahoo reference-close policy for `GOLD` and `BRENT_OIL`: raw provider daily closes, provider-managed continuous-futures include-all rollover, and no validated fallback. The service must preserve these semantics and must not describe those closes as official exchange settlements.

## 5. Persistence

Minimum records:

- Price request: request/prediction/asset IDs, two sessions, calendar, state, attempts, next retry, errors, timestamps.
- Close observation: asset ID, session, close, source, provider symbol, provider bar time, fetch time, price kind, adjustment flag, registry version, and immutable content hash/version.
- Outbox: the canonical `PriceObserved` publication.

The uniqueness rules prevent duplicate requests and duplicate provider observations from producing duplicate outputs.

## 6. Retry and failure policy

- Market-not-yet-closed and provider-lag responses remain pending with a bounded next attempt.
- Transient provider failures use bounded exponential backoff with jitter.
- Invalid instrument/session/currency data are terminal and quarantined.
- Poison messages go to the work queue DLQ with failure metadata.
- Replaying a request with the same ID reuses durable work and observations.

## 7. Operations

- `/health` reports liveness.
- `/ready` checks PostgreSQL, RabbitMQ, the persistent scheduler, and required registry entries; it need not call providers on every probe.
- `GET /prices/recent?asset_id=<canonical>&sessions=<N>` returns the most recent stored session closes for an asset (canonical IDs only, closes as decimal strings). It is a read-only projection used by Prediction to derive the `RISK_PREMIUM_ELEVATED` condition; it triggers no provider fetch.
- Shutdown stops new consumption, persists scheduler state, safely handles in-flight fetches, and closes connections.
- Logs use canonical asset ID and correlation IDs; provider symbols appear only in adapter/audit fields.

## 8. Acceptance criteria

1. RabbitMQ acknowledgement occurs only after scheduled work is durable.
2. Restarting restores pending baseline, settlement, and publish work.
3. One request produces one `PriceObserved` containing both exact closes.
4. Duplicate delivery does not create a second schedule or publication identity.
5. Canonical IDs cross service boundaries; provider symbols remain adapter metadata.
6. Wrong session, instrument, currency, adjustment, registry version, or rollover state is rejected.
7. The service never calculates prediction correctness.
8. Provider daily closes are never labelled official settlements.
