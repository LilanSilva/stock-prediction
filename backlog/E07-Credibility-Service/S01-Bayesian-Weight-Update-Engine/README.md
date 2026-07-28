# S01 - Bayesian Weight Update Engine

## Overview

This story builds the complete Credibility Service: a single consumer that reads `PredictionScored` events and applies a Beta-Bernoulli Bayesian update to two independent dimensions of credibility:

1. **Causal graph edge weights** — each edge in the Neo4j knowledge graph (e.g. `war -> gold: +0.7`) gets its `alpha` and `beta` updated, which drives the `credibility_score` used by the Prediction Service on the next inference cycle.
2. **News source credibility** — each domain (e.g. `di.se`, `svd.se`) that contributed articles to the prediction gets its own independent Beta-Bernoulli score tracked in Postgres.

A full audit trail with 95% confidence intervals is stored in `credibility_history` so the Dashboard can plot uncertainty bands over time.

## Tasks

| Task | Description |
|---|---|
| T01 - Beta-Bernoulli edge weight updater | Consume PredictionScored, apply proportional Bayesian credit to Neo4j edge properties |
| T02 - Source credibility tracker | Track per-domain credibility independently in Postgres using the same Beta-Bernoulli model |
| T03 - Credibility history & confidence intervals | Persist full update history with 95% CI bounds to `credibility_history` table |

## Dependencies

Before this story can start the following must exist:

- **E05 / Verification Service** — must publish valid `PredictionScored` messages to the `scored-predictions` RabbitMQ queue. The `contributing_edges` and `sources` arrays must be populated.
- **Neo4j knowledge graph** — causal edges must exist with at least `edge_id`, `alpha`, and `beta` properties. The graph is seeded by the data engineering setup (see `infra/neo4j/`).
- **Postgres schema** — `credibility` and `credibility_history` tables must be created (migrations in `infra/postgres/`).
- **`src/shared/` library** — `PredictionScored` Pydantic schema, RabbitMQ client wrapper, and database session factories must exist.

## How to Test the Story End-to-End

1. Start the full stack: `docker compose up` in `infra/`.
2. Manually publish a synthetic `PredictionScored` message to the `scored-predictions` queue (use the RabbitMQ Management UI at `http://localhost:15672` or a helper script):
   ```json
   {
     "prediction_id": "test-pred-001",
     "asset": "XAUUSD",
     "predicted_direction": "UP",
     "actual_direction": "UP",
     "predicted_magnitude": "MEDIUM",
     "actual_magnitude": "MEDIUM",
     "is_correct": true,
     "score": 1.0,
     "contributing_edges": [
       {"edge_id": "war->gold", "influence_weight": 0.7},
       {"edge_id": "inflation->gold", "influence_weight": 0.3}
     ],
     "sources": ["di.se", "svd.se"],
     "scored_at": "2026-06-30T12:00:00Z"
   }
   ```
3. Query Neo4j: `MATCH ()-[r {edge_id: 'war->gold'}]-() RETURN r.alpha, r.beta, r.credibility_score` — alpha should have increased by ~0.7 relative to its prior value.
4. Query Postgres `credibility` table: both `di.se` and `svd.se` rows should show alpha incremented by 0.5 (equal split across 2 sources).
5. Query Postgres `credibility_history`: two new rows for edges and two for sources, all with `prediction_id = 'test-pred-001'` and valid `ci_lower` / `ci_upper` values.
6. Run unit tests: `pytest src/services/credibility/tests/ -v`.
