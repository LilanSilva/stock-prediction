# T01: Beta-Bernoulli Edge Weight Updater

## Context

This task implements the core feedback loop of the entire prediction system. The Credibility Service (`src/services/credibility/`) consumes `PredictionScored` messages from the `scored-predictions` RabbitMQ queue and updates the causal graph edge weights in Neo4j using a Beta-Bernoulli Bayesian model with proportional credit assignment. Updated edge weights are used by the Prediction Service on the next inference cycle, making the system self-improving over time.

## Background

### Beta-Bernoulli Model

Each causal edge in Neo4j (e.g. `war -> gold: +0.7`) carries two properties:
- `alpha` (float): count of successful predictions this edge contributed to (hits), initialised to 1.0
- `beta` (float): count of failed predictions (misses), initialised to 1.0
- `credibility_score` (float): the current point estimate = `alpha / (alpha + beta)`

Starting at alpha=1, beta=1 gives a uniform prior (credibility=0.5, maximum uncertainty). As evidence accumulates the score converges toward the true hit rate.

### Proportional Credit Assignment

A single prediction may involve multiple contributing edges (e.g. `war->gold` with influence 0.7 and `inflation->gold` with influence 0.3). Rather than giving full credit to every edge, credit is proportional to each edge's relative influence weight:

```
credit_i = influence_weight_i / sum(all influence_weights in this prediction)
```

On a **hit** (`is_correct = True`): `alpha_i += credit_i`  
On a **miss** (`is_correct = False`): `beta_i += credit_i`

This means a highly influential edge earns more credit/blame than a peripheral one.

### Message Schema

The incoming `PredictionScored` message (from `src/shared/schemas.py`) has these relevant fields:
- `prediction_id: str`
- `is_correct: bool`
- `contributing_edges: list[ContributingEdge]` where `ContributingEdge` has `edge_id: str` and `influence_weight: float`
- `scored_at: datetime`

### Neo4j Edge Properties

Each edge in Neo4j must have:
- `edge_id: str` — unique identifier matching what `contributing_edges[].edge_id` references
- `alpha: float` — default 1.0
- `beta: float` — default 1.0
- `credibility_score: float` — derived: `alpha / (alpha + beta)`

## Inputs

- **RabbitMQ queue:** `scored-predictions`
- **Message type:** `PredictionScored` (defined in `src/shared/schemas.py`)
- **Neo4j:** existing causal edges with `edge_id`, `alpha`, `beta`, `credibility_score` properties
- **Environment variables:**
  - `RABBITMQ_URL` (e.g. `amqp://guest:guest@rabbitmq:5672/`)
  - `NEO4J_URI` (e.g. `bolt://neo4j:7687`)
  - `NEO4J_USER`
  - `NEO4J_PASSWORD`

## Outputs

- **Neo4j edge updates:** for each `contributing_edge` in the message, the edge's `alpha`, `beta`, and `credibility_score` properties are updated in-place.
- **Postgres `credibility` table row (upsert):** one row per `edge_id` capturing the current state post-update. Schema:
  ```sql
  CREATE TABLE credibility (
    entity_id       TEXT NOT NULL,          -- edge_id or source_domain
    entity_type     TEXT NOT NULL,          -- 'edge' or 'source'
    alpha           FLOAT NOT NULL DEFAULT 1.0,
    beta            FLOAT NOT NULL DEFAULT 1.0,
    credibility_score FLOAT NOT NULL,
    last_updated    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (entity_id, entity_type)
  );
  ```

## Technical Requirements

1. **Service entrypoint:** `src/services/credibility/main.py` — starts an `asyncio` event loop, connects to RabbitMQ using `aio-pika`, and begins consuming the `scored-predictions` queue.

2. **Queue consumption:** use `aio-pika` with `manual_ack=True` on a durable queue named `scored-predictions`. Only `ack` the message after all Neo4j and Postgres writes succeed. On failure, `nack` with `requeue=True`.

3. **Message parsing:** deserialise the message body as `PredictionScored` from `shared.schemas`. Raise `pydantic.ValidationError` on malformed messages — log the error, `ack` the message (do not requeue poison pills), and continue.

4. **Credit calculation function** (in `src/services/credibility/updater.py`):
   ```python
   def compute_proportional_credits(
       edges: list[ContributingEdge]
   ) -> dict[str, float]:
       ...
   ```
   Must normalise by the sum of all `influence_weight` values. If the sum is zero (degenerate case), fall back to equal credit distribution (1/n per edge).

5. **Neo4j update:** use `neo4j` Python driver (`neo4j>=5.0`). Use a single Cypher `MATCH` + `SET` per edge within a transaction:
   ```cypher
   MATCH ()-[r]-() WHERE r.edge_id = $edge_id
   SET r.alpha = CASE WHEN r.alpha IS NULL THEN 1.0 ELSE r.alpha END + $alpha_delta,
       r.beta  = CASE WHEN r.beta  IS NULL THEN 1.0 ELSE r.beta  END + $beta_delta,
       r.credibility_score = (CASE WHEN r.alpha IS NULL THEN 1.0 ELSE r.alpha END + $alpha_delta)
                             / ((CASE WHEN r.alpha IS NULL THEN 1.0 ELSE r.alpha END + $alpha_delta)
                                + (CASE WHEN r.beta IS NULL THEN 1.0 ELSE r.beta END + $beta_delta))
   RETURN r.edge_id, r.alpha, r.beta, r.credibility_score
   ```
   Execute all edge updates for one `PredictionScored` message inside a single Neo4j transaction (atomic batch).

6. **Alpha/beta floor:** after computing the updated value, enforce `max(value, 1.0)` before writing. This ensures the uninformed prior is never undershot (e.g. if seed data sets alpha=0).

7. **Postgres upsert** (use `asyncpg` or `sqlalchemy[asyncio]` with the shared session factory):
   ```sql
   INSERT INTO credibility (entity_id, entity_type, alpha, beta, credibility_score, last_updated)
   VALUES ($1, 'edge', $2, $3, $4, NOW())
   ON CONFLICT (entity_id, entity_type)
   DO UPDATE SET alpha=$2, beta=$3, credibility_score=$4, last_updated=NOW();
   ```

8. **Retry logic:** wrap Neo4j and Postgres writes in a retry decorator with exponential backoff (`tenacity` library: `wait_exponential(multiplier=1, min=1, max=30)`, `stop_after_attempt(5)`).

9. **Logging:** use `structlog` (from shared library). Log `prediction_id`, number of edges updated, and wall-clock duration per message at INFO level. Log individual edge updates at DEBUG level.

10. **Dependency file:** `src/services/credibility/requirements.txt` must include:
    - `aio-pika>=9.0`
    - `neo4j>=5.0`
    - `asyncpg>=0.29`
    - `sqlalchemy[asyncio]>=2.0`
    - `tenacity>=8.0`
    - `pydantic>=2.0`
    - `structlog>=24.0`

## Acceptance Criteria

1. Given a `PredictionScored` message with `is_correct=True` and two contributing edges (weights 0.7 and 0.3), after processing, the first edge's `alpha` in Neo4j increases by exactly `0.7` (±0.0001) and the second's by `0.3` (±0.0001). Neither edge's `beta` changes.

2. Given a `PredictionScored` message with `is_correct=False` and the same edges, `beta` values increase by 0.7 and 0.3 respectively. Neither `alpha` changes.

3. `credibility_score` stored in Neo4j equals `alpha / (alpha + beta)` to 6 decimal places after each update.

4. If an edge listed in `contributing_edges` does not exist in Neo4j, the service logs a WARNING with the missing `edge_id` and continues processing the remaining edges without failing the message.

5. If Neo4j is unavailable, the message is `nack`-ed with `requeue=True` and the service retries with backoff. No message is lost.

6. A malformed/unparseable message is `ack`-ed (removed from queue) after logging an ERROR. The service continues processing subsequent messages.

7. The Postgres `credibility` table contains an up-to-date row for each processed edge with correct `alpha`, `beta`, `credibility_score`, and `entity_type='edge'`.

8. `alpha` and `beta` values in Neo4j and Postgres are always >= 1.0.

9. `pytest src/services/credibility/tests/test_updater.py` passes with all unit tests for `compute_proportional_credits` and the update logic.

10. `ruff check src/services/credibility/` and `mypy src/services/credibility/` both exit 0.

## Implementation Notes

- **Neo4j relationship direction:** causal edges may be directional (`(cause)-[r:CAUSES]->(effect)`). The `MATCH ()-[r]-()` pattern is direction-agnostic — use it to avoid edge-direction assumptions breaking lookups.
- **Proportional credit with a single edge:** if `contributing_edges` has exactly one entry, credit = 1.0 regardless of its `influence_weight`. This is mathematically correct (normalisation).
- **Idempotency concern:** if the same `PredictionScored` message is delivered twice (RabbitMQ at-least-once), alpha/beta will be double-counted. Mitigate by recording processed `prediction_id` values in a `processed_predictions` Postgres table and skipping duplicates. This is not required for MVP but the code should be structured to allow adding it later (i.e. the update function should be a separate, testable unit).
- **Transaction scope:** wrap all Neo4j edge updates for a single message in one transaction. If the Postgres write fails after a successful Neo4j commit, log a CRITICAL error with the `prediction_id` so it can be manually replayed. Full two-phase commit is out of scope for MVP.
- **Neo4j driver session management:** use a module-level `neo4j.AsyncDriver` instance (not per-message) to avoid connection pool exhaustion.
- **Degenerate weight sum:** guard against `sum(influence_weights) == 0.0` with equal distribution fallback — this can happen if upstream data is malformed.

## Definition of Done

- [ ] Unit tests pass (`pytest src/services/credibility/tests/`)
- [ ] `ruff check src/services/credibility/` exits 0
- [ ] `mypy src/services/credibility/` exits 0
- [ ] `compute_proportional_credits` is tested with: normal case, single edge, zero-sum weights, empty list
- [ ] Neo4j update is tested with a mock driver asserting the correct Cypher parameters
- [ ] Postgres upsert is tested with an in-memory or mock session
- [ ] Message ack/nack behaviour is tested for success, Neo4j failure, and malformed message cases
- [ ] `src/services/credibility/requirements.txt` lists all dependencies with minimum version pins
- [ ] `src/services/credibility/Dockerfile` exists and builds successfully
