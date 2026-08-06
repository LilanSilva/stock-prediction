# Dashboard Functional Document

> **Status: not built.** This is design intent for epic E09, not a specification of running code.
> Moved here from `docs/functional-documents/` on 2026-08-06 because
> [requirements/](../../requirements/README.md) holds specifications for implemented components only —
> an SRS-09 will be written when the Dashboard is built. Where this document disagrees with
> [requirements/](../../requirements/README.md), the requirements win.

## 1. Purpose

The Dashboard is a local POC interface for inspecting events, predictions, outcomes, prices, and learned credibility. It visualizes system state but does not perform prediction, verification, or learning.

## 2. Required views

- Prediction list with event context, canonical asset, direction, horizon, confidence, decision method, status, and score.
- Prediction detail with contributing event IDs, graph edges, provenance, baseline close, settlement close, and scoring explanation.
- Accuracy over time and by asset/horizon.
- Edge, arbiter, and source predictive-utility views kept as separate concepts.
- Knowledge-graph view for relevant causal paths.
- Live prediction and score updates through the Gateway WebSocket.

## 3. Data rules

- All data comes through the API Gateway; the browser never connects directly to PostgreSQL, Neo4j, or RabbitMQ.
- UI filtering and labels use the canonical asset registry and event taxonomy.
- `unknown` or missing values are displayed explicitly and are not converted to zero.
- Price outcomes show both `baseline_session` and `settlement_session` so close-to-close scoring is auditable.
- The UI distinguishes graph-only predictions from LLM-arbitrated predictions.

## 4. Token-cost visibility

When LLM metadata is available, prediction details may show model, input tokens, output tokens, cache status, retry count, and decision reason. This is observability only and must not trigger an LLM request.

## 5. POC constraints

- Runs locally and targets desktop use.
- No browser-side secrets.
- No administrative mutation controls.
- Authentication and multi-user authorization are deferred while access remains localhost-only.

## 6. Error and connection behavior

- Loading, empty, and error states are distinct.
- WebSocket reconnection uses bounded exponential backoff.
- A reconnect triggers a REST refresh before subsequent live messages are applied.
- Duplicate live messages are ignored by `message_id`.

## 7. Acceptance criteria

1. A user can trace a prediction from its event context to its eventual score.
2. Canonical IDs and labels are used consistently across all views.
3. Baseline and settlement observations are visible for scored predictions.
4. Edge, arbiter, and source learning metrics are never merged into one score.
5. Live updates do not duplicate records after reconnect.
6. The Dashboard causes no LLM calls and contains no business-rule implementation.

