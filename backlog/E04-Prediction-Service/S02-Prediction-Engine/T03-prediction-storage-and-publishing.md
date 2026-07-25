# T03: Prediction Storage and Publishing

## Current Decision

After the POC-6 `STOP` result, M1 Prediction Service stores and publishes graph-only predictions only. It does not call an LLM and does not publish `PriceRequested`.

## Purpose

Persist each immutable `PredictionMade` record and publish it through the canonical `feed.events` exchange with routing key `prediction.made`.

Verification is the sole owner of evaluation scheduling and `price.requested` publication.

## Inputs

- `PredictionMade` Pydantic schema instance from the graph-only decision policy.
- Contributing graph edges and context provenance.
- Correlation/message metadata from the consumed event/context.

## Outputs

- PostgreSQL `prediction` schema records:
  - prediction ID
  - context ID and version
  - canonical `asset_id`
  - direction, magnitude, confidence
  - horizon `ONE_TRADING_DAY`
  - rationale
  - contributing edges
  - decision method `GRAPH_ONLY`
  - optional `llm_metadata=null`
  - idempotency key
- Outbox row for `prediction.made`.
- RabbitMQ publication to `feed.events` with routing key `prediction.made`.

## Requirements

- Use canonical `asset_id`, never provider symbols.
- Prediction idempotency key is `(asset_id, context_window_start, horizon, context_version)`.
- Duplicate delivery must not create a second prediction.
- Persist before publish through outbox or equivalent reconciliation.
- Publish only `PredictionMade`; do not publish `PriceRequested`.
- Store `llm_metadata=null` for M1 graph-only predictions.
- A future LLM arbitration experiment may populate LLM metadata only after a new controlled hypothesis is approved.

## Acceptance Criteria

1. One ready asset/context version creates at most one prediction row.
2. Replaying the same context version does not create a duplicate prediction.
3. The published message conforms to canonical `PredictionMade`.
4. The service publishes `prediction.made` only.
5. No `price.requested` message is published by Prediction.
6. `decision_method=GRAPH_ONLY` for M1 predictions.
7. `llm_metadata` is null for M1 predictions.
8. Outbox recovery republishes unpublished predictions without creating duplicate rows.
