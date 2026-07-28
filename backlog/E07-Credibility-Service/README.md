# E07 - Credibility Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

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

1. The service consumes `PredictionScored` messages from the `scored-predictions` RabbitMQ queue without message loss (durable queue, manual ack).
2. After processing a scored prediction, every `contributing_edge` listed in the message has its `alpha`, `beta`, and `credibility_score` properties updated in Neo4j using proportional credit assignment.
3. After processing a scored prediction, every `source` listed in the message has its `alpha`, `beta`, and `credibility_score` updated in the Postgres `credibility` table.
4. The `credibility_history` Postgres table contains one row per update event per edge/source, with before/after alpha/beta values, the `prediction_id` that caused the update, and 95% Beta confidence interval bounds.
5. Proportional credit is correctly normalised: the sum of all credit fractions for a single `PredictionScored` message equals 1.0 for edges and 1.0 for sources independently.
6. Alpha and beta never fall below 1.0 (uninformed prior is enforced as a floor).
7. The service recovers cleanly from Neo4j or Postgres transient failures (retry with backoff).
8. All code passes `ruff` linting and `mypy` strict type checking.
9. Unit test coverage >= 90% for the Bayesian update logic.
