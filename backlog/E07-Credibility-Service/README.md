# E07 - Credibility Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

> As-built (implemented 2026-07-29): the service is built at `src/services/credibility/` and wired into `infra/docker-compose.yml` as `feed-credibility` (port 8007), consuming the **`credibility.scored`** queue bound to routing key **`prediction.scored`** on `feed.events`. It aligns with the frozen contract (`shared.schemas.messages.PredictionScored`), which differs from the legacy draft below in several ways that are non-authoritative where they conflict:
> - The canonical message carries `contributing_edges` (each `edge_id` is the deterministic business key `"FACTOR->ASSET"`, e.g. `MILITARY_CONFLICT->GOLD`, with an `influence_weight` in [0,1]) and **`source_ids`** (a plain domain list) — not signed weights, not a field named `sources`.
> - Neo4j holds exactly one `(:CausalFactor)-[:CAUSES]->(:Asset)` edge per factor/asset pair carrying `alpha`/`beta`; updates go through the shared `CausalGraphClient` (read via `get_firing_edges`, write via `update_edge_weight`), not an arbitrary `edge_id` string property.
> - Postgres uses the service-owned **`credibility`** schema in the single `feed` database (tables `credibility`, `credibility_history`, `processed_predictions`) — not a per-service database.
> - The 95% Beta credible interval is computed in **pure Python** (regularized incomplete beta + bisection, unit-tested against known values); scipy/numpy are intentionally not a dependency, keeping the image as lean as the sibling services.
> - An **idempotency guard** (`processed_predictions`) makes at-least-once redelivery a no-op, so Beta counts are never double-counted.
> - The service **publishes nothing**; its only outputs are the write-back to Neo4j edge weights and the Postgres source scores + history.

## Overview

The Credibility Service is the feedback loop that makes the prediction system self-improving. It consumes `PredictionScored` messages from the `scored-predictions` queue and applies a Beta-Bernoulli Bayesian model to update the credibility weights of the causal graph edges in Neo4j and the news source credibility scores in Postgres.

This service closes the pipeline loop: every prediction that gets scored feeds back into the knowledge graph so future predictions use better-calibrated weights.

## Stories

| Story | Description |
|---|---|
| S01 - Bayesian Weight Update Engine | Beta-Bernoulli updates to edge weights and source credibility scores, with full history and confidence intervals stored in Postgres |

## Architecture Context

**Service:** `src/services/credibility/`

**Consumes queue:** `scored-predictions` (message: `PredictionScored`)

**Writes to:**
- Neo4j: updates `alpha`, `beta`, and `credibility_score` properties on causal graph edges
- Postgres: `credibility` table (current state per edge / source), `credibility_history` table (full audit trail per update event)

**Does NOT publish to any queue.** The output is a direct write back to the knowledge graph.

```
scored-predictions queue
        |
        v
  Credibility Service
        |
        |---> Neo4j: update edge weights (alpha/beta/credibility_score)
        |---> Postgres: credibility table (current state)
        '---> Postgres: credibility_history table (audit trail + CI bands)
```

## Overall Acceptance Criteria

> The list below is the original acceptance criteria. Items are retained for history; where the
> queue name or field names conflict with the frozen contract, the parenthetical note records what
> was actually built. The authoritative outcome is the **As-built acceptance** list that follows.

1. The service consumes `PredictionScored` messages from the `scored-predictions` RabbitMQ queue without message loss (durable queue, manual ack). *(As-built: the queue is **`credibility.scored`**, durable and bound to `prediction.scored`; the shared client acks only after the callback returns, dead-letters poison messages to `credibility.scored.dlq`, and retries transient failures.)*
2. After processing a scored prediction, every `contributing_edge` listed in the message has its `alpha`, `beta`, and `credibility_score` properties updated in Neo4j using proportional credit assignment. *(As-built: `alpha`/`beta` are updated on the `CAUSES` edge via the shared client; `credibility_score` is derived, not stored on the edge.)*
3. After processing a scored prediction, every `source` listed in the message has its `alpha`, `beta`, and `credibility_score` updated in the Postgres `credibility` table. *(As-built: sources come from the canonical **`source_ids`** field.)*
4. The `credibility_history` Postgres table contains one row per update event per edge/source, with before/after alpha/beta values, the `prediction_id` that caused the update, and 95% Beta confidence interval bounds.
5. Proportional credit is correctly normalised: the sum of all credit fractions for a single `PredictionScored` message equals 1.0 for edges and 1.0 for sources independently.
6. Alpha and beta never fall below 1.0 (uninformed prior is enforced as a floor).
7. The service recovers cleanly from Neo4j or Postgres transient failures (retry with backoff). *(As-built: transient failures are retried-then-dead-lettered by the shared RabbitMQ client's redelivery policy; the idempotency guard makes redelivery safe.)*
8. All code passes `ruff` linting and `mypy` strict type checking.
9. Unit test coverage >= 90% for the Bayesian update logic.

### As-built acceptance (implemented 2026-07-29)

- [x] Consumes the durable `credibility.scored` queue via the shared `RabbitMQClient`; poison messages dead-letter to `credibility.scored.dlq`.
- [x] Proportional Beta-Bernoulli credit is applied to each contributing edge's `alpha`/`beta` in Neo4j through the shared `CausalGraphClient` (read-modify-write); a missing edge is logged and skipped without failing the message.
- [x] Equal credit is applied to each `source_ids` domain in the Postgres `credibility.credibility` table (read-modify-write, upsert), with `credibility_score = alpha / (alpha + beta)`.
- [x] `credibility.credibility_history` records one append-only row per edge and per source with before/after `alpha`/`beta`, `credibility_before`/`after`, `prediction_id`, and 95% Beta CI bounds.
- [x] Credit fractions sum to 1.0 for edges and 1.0 for sources independently (degenerate zero-sum edge weights fall back to equal split).
- [x] `alpha`/`beta` are floored at the uninformed prior (1.0) on every write.
- [x] An idempotency guard (`credibility.processed_predictions`) makes at-least-once redelivery a no-op — Beta counts are never double-counted.
- [x] `ruff check` and `mypy --strict` pass on `src/services/credibility`; 27 unit tests + 2 live integration tests green.
- [x] The service publishes nothing; `feed-credibility` runs healthy in Docker with `/health` + `/ready` (postgres, rabbitmq, neo4j) green, and has applied real `PredictionScored` messages end-to-end (edge write-back verified in Neo4j).
