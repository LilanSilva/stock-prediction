# T03: Prediction Storage and Publishing

> **Delivered:** implemented as `prediction/repository.py` (transactional prediction + contributing
> edges + outbox), `prediction/outbox.py` (relay to `prediction.made`), and `prediction/db.py`
> (`prediction` schema). Idempotency key `(asset_id, window_start, ONE_TRADING_DAY, context_version)`.
> `decision_method=GRAPH_ONLY`, `llm_metadata=null`. Prediction never publishes `PriceRequested`.

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

### Stance deduplication (no-noise rule)

Before persisting a new prediction, the service must check the asset's latest **active prediction**
— defined as the most recent row with `status = 'PENDING'`. The check and the insert must be
performed inside the context-close sweep, after the decision policy returns a result:

| Market state | Prior PENDING exists | New matches prior `(direction, magnitude)` | Action |
|---|---|---|---|
| Trading day | Yes | Yes — same direction AND same magnitude | **Skip**: mark context PREDICTED, produce no row, publish nothing |
| Trading day | Yes | No — direction or magnitude differs | **Create** new independent prediction; `supersedes_prediction_id = NULL` |
| Trading day | No | n/a | **Create** new prediction; `supersedes_prediction_id = NULL` |
| Market closed | Yes | Either | **Supersede**: new prediction sets `supersedes_prediction_id`, prior is marked WITHDRAWN in same transaction |
| Market closed | No | n/a | **Create** new prediction; `supersedes_prediction_id = NULL` |

The intent is to prevent the prediction table from accumulating duplicate signals that would create
confusion in Verification and Credibility. Different direction or magnitude always represents new
information and must not be suppressed.

Once a prior prediction is scored (status no longer PENDING), it no longer participates in stance
checks: the asset effectively has no active prediction and a new one is created freely.

## Acceptance Criteria

1. One ready asset/context version creates at most one prediction row.
2. Replaying the same context version does not create a duplicate prediction.
3. The published message conforms to canonical `PredictionMade`.
4. The service publishes `prediction.made` only.
5. No `price.requested` message is published by Prediction.
6. `decision_method=GRAPH_ONLY` for M1 predictions.
7. `llm_metadata` is null for M1 predictions.
8. Outbox recovery republishes unpublished predictions without creating duplicate rows.
9. On a trading day, a new decision with `(direction, magnitude)` identical to the latest PENDING
   prediction produces no new row and no published message.
10. On a trading day, a new decision with a different direction or magnitude creates an independent
    new prediction row; the prior PENDING prediction is unaffected.
11. On a market-closed day, a new prediction supersedes the prior PENDING prediction, which is
    marked WITHDRAWN in the same DB transaction; only one PENDING prediction per asset remains.
12. After the prior prediction is scored (no longer PENDING), a new prediction with the same
    direction and magnitude is created normally — the stance check only applies to PENDING rows.
