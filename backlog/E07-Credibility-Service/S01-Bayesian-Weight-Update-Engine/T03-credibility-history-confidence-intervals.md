# T03: Credibility History & Confidence Intervals

## Context

This task adds a full audit trail to the Credibility Service (`src/services/credibility/`). Every time an edge weight or source credibility score is updated (by T01 and T02 respectively), a history row is written to the `credibility_history` Postgres table capturing the before/after state along with 95% Beta distribution confidence interval bounds. The Dashboard uses this history to plot credibility trends with uncertainty bands over time.

## Background

### Why Store History?

The `credibility` table (T01/T02) only stores the current state — it's an upsert table. The `credibility_history` table is an append-only log of every update event. This enables:
1. Time-series plots of how a source or edge credibility evolves as more predictions are scored
2. Uncertainty bands (confidence intervals) showing how confident we are in the credibility estimate
3. Auditability: which `prediction_id` caused each weight change

### 95% Beta Confidence Interval

The Beta distribution `Beta(alpha, beta)` models the true probability of a Bernoulli success (a correct prediction). The 95% credible interval is the range `[lower, upper]` such that 95% of the posterior probability mass falls within it:

```python
from scipy.stats import beta as beta_dist

lower, upper = beta_dist.interval(0.95, alpha, beta)
```

Example: `Beta(5, 3)` → credibility_score = 0.625, CI = [0.295, 0.893]. Wide interval means few observations; narrow interval means many observations.

With `alpha=1, beta=1` (prior): CI = [0.025, 0.975] — maximum uncertainty.
With `alpha=50, beta=10`: CI = [0.718, 0.917] — high confidence.

### Postgres Table Schema

```sql
CREATE TABLE credibility_history (
  id              BIGSERIAL PRIMARY KEY,
  entity_id       TEXT NOT NULL,        -- edge_id or source_domain
  entity_type     TEXT NOT NULL,        -- 'edge' or 'source'
  prediction_id   TEXT NOT NULL,        -- which PredictionScored caused this
  alpha_before    FLOAT NOT NULL,
  beta_before     FLOAT NOT NULL,
  alpha_after     FLOAT NOT NULL,
  beta_after      FLOAT NOT NULL,
  credibility_before FLOAT NOT NULL,    -- alpha_before / (alpha_before + beta_before)
  credibility_after  FLOAT NOT NULL,    -- alpha_after  / (alpha_after  + beta_after)
  ci_lower        FLOAT NOT NULL,       -- 95% CI lower bound (post-update)
  ci_upper        FLOAT NOT NULL,       -- 95% CI upper bound (post-update)
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_credibility_history_entity
  ON credibility_history (entity_id, entity_type, updated_at DESC);

CREATE INDEX idx_credibility_history_prediction
  ON credibility_history (prediction_id);
```

## Inputs

- **The `alpha_before` / `beta_before` values** — must be captured by T01/T02 *before* the update is applied. T01 and T02 must return or pass through old values to the history writer.
- **The `alpha_after` / `beta_after` values** — computed by T01/T02 after applying proportional credit.
- **`prediction_id`** — from the `PredictionScored` message being processed.
- **`entity_id`** and **`entity_type`** — `edge_id` / `'edge'` or `source_domain` / `'source'`.
- **Environment variable:** `POSTGRES_DSN`
- **Python library:** `scipy>=1.12` (for `scipy.stats.beta.interval`)

## Outputs

- **Postgres `credibility_history` table inserts:** one row per entity (edge or source) per `PredictionScored` message. Never updated — append-only.

## Technical Requirements

1. **New module:** `src/services/credibility/history.py` — contains the history writing logic, separate from `updater.py`.

2. **History writer function:**
   ```python
   from scipy.stats import beta as beta_dist

   async def write_credibility_history(
       session: AsyncSession,
       entity_id: str,
       entity_type: str,  # Literal['edge', 'source']
       prediction_id: str,
       alpha_before: float,
       beta_before: float,
       alpha_after: float,
       beta_after: float,
   ) -> None:
       ci_lower, ci_upper = beta_dist.interval(0.95, alpha_after, beta_after)
       credibility_before = alpha_before / (alpha_before + beta_before)
       credibility_after  = alpha_after  / (alpha_after  + beta_after)
       # INSERT INTO credibility_history ...
   ```

3. **Integration into T01/T02 flow:** modify the update functions in `updater.py` to return `(alpha_before, beta_before, alpha_after, beta_after)`. The `handle_prediction_scored` function in `main.py` calls `write_credibility_history` for every edge and source updated, within the **same Postgres transaction** as the `credibility` upsert.

4. **Atomic transaction:** both the `credibility` upsert (current state) and the `credibility_history` insert (audit row) must be committed in the same Postgres transaction. If the history insert fails, the credibility upsert rolls back too, and the message is `nack`-ed.

5. **`scipy.stats.beta.interval` call:** this is a pure Python CPU call (no I/O). Call it synchronously within the async function — it is fast enough (<1ms) that wrapping in `asyncio.to_thread` is not required.

6. **Edge case — `alpha_after` or `beta_after` is very small:** `scipy.stats.beta.interval` requires both shape parameters > 0. Since we enforce a floor of 1.0 (T01/T02), both will always be >= 1.0. No special handling needed, but add an assertion.

7. **Batch inserts:** for a message with many contributing edges and sources, batch all `credibility_history` inserts into a single `INSERT ... VALUES ($1,$2,...), ($3,$4,...), ...` statement for efficiency. Use `asyncpg` executemany or SQLAlchemy bulk insert.

8. **Add `scipy` to requirements:**
   ```
   scipy>=1.12
   ```
   in `src/services/credibility/requirements.txt`.

9. **Postgres migration:** add `credibility_history` table and both indexes to the migration script at `infra/postgres/migrations/`. This migration runs after the `credibility` table migration from T01.

10. **Logging:** log the number of history rows written per message at DEBUG level.

## Acceptance Criteria

1. After processing a `PredictionScored` message with 2 contributing edges and 3 sources, the `credibility_history` table contains exactly 5 new rows (2 edge rows + 3 source rows) each with `prediction_id` matching the message.

2. For each history row, `alpha_before` and `beta_before` match the values that were in the `credibility` table *before* the update was applied.

3. For each history row, `alpha_after` and `beta_after` match the values written to the `credibility` table *after* the update.

4. `credibility_before = alpha_before / (alpha_before + beta_before)` to 6 decimal places.

5. `credibility_after = alpha_after / (alpha_after + beta_after)` to 6 decimal places.

6. `ci_lower` and `ci_upper` are the correct 95% Beta credible interval bounds for `Beta(alpha_after, beta_after)`, as returned by `scipy.stats.beta.interval(0.95, alpha_after, beta_after)`, to 4 decimal places.

7. `0.0 <= ci_lower < ci_upper <= 1.0` for all rows.

8. If Postgres fails mid-transaction, neither the `credibility` upsert nor the `credibility_history` insert is committed (both roll back together). The message is `nack`-ed.

9. The `credibility_history` table is append-only: existing rows are never modified (no UPDATE statements in `history.py`).

10. `pytest src/services/credibility/tests/test_history.py` passes with >= 90% coverage on `history.py`.

11. `ruff check src/services/credibility/` and `mypy src/services/credibility/` both exit 0.

## Implementation Notes

- **Capturing `alpha_before`:** the read-modify-write approach recommended in T02 naturally gives you `alpha_before` and `beta_before` (they are the values fetched before applying the delta). T01 uses Neo4j's `RETURN` clause to get the updated values — you must `RETURN` the pre-update values too, or fetch current values before executing the update Cypher. Simplest approach: use `MATCH ... RETURN r.alpha, r.beta` before the `SET` in T01, or restructure as `MATCH ... WITH r.alpha AS alpha_before, r.beta AS beta_before, r SET ...`.
- **Cypher to capture before/after in one query:**
  ```cypher
  MATCH ()-[r]-() WHERE r.edge_id = $edge_id
  WITH r,
       coalesce(r.alpha, 1.0) AS alpha_before,
       coalesce(r.beta,  1.0) AS beta_before
  SET r.alpha = alpha_before + $alpha_delta,
      r.beta  = beta_before  + $beta_delta,
      r.credibility_score = (alpha_before + $alpha_delta)
                            / ((alpha_before + $alpha_delta) + (beta_before + $beta_delta))
  RETURN alpha_before, beta_before, r.alpha AS alpha_after, r.beta AS beta_after
  ```
  This returns all four values in one round-trip.
- **`scipy` import:** `from scipy.stats import beta as beta_dist` — do not shadow Python's built-in `beta` name if you were using it elsewhere.
- **CI interpretation for the dashboard:** a wider CI band signals low confidence (fewer observations). The Dashboard should display this as a shaded area around the credibility line. Store `ci_lower` and `ci_upper` as columns — the API Gateway returns them directly without recomputing.
- **Index performance:** the `idx_credibility_history_entity` index on `(entity_id, entity_type, updated_at DESC)` allows the Dashboard query `SELECT * FROM credibility_history WHERE entity_id=$1 AND entity_type=$2 ORDER BY updated_at DESC LIMIT 100` to use an index scan.
- **Historical volume:** over time this table will grow large. For MVP, no partitioning is needed. Document a note in `infra/postgres/migrations/` that range partitioning by `updated_at` (monthly) should be added when the table exceeds 10M rows.

## Definition of Done

- [ ] Unit tests pass (`pytest src/services/credibility/tests/test_history.py`)
- [ ] `ruff check src/services/credibility/` exits 0
- [ ] `mypy src/services/credibility/` exits 0
- [ ] `write_credibility_history` is tested with: edge entity type, source entity type, prior state (alpha=1, beta=1), mature state (alpha=50, beta=10)
- [ ] CI computation is tested against known values from `scipy.stats.beta.interval`
- [ ] Atomic transaction rollback is tested: simulate Postgres failure after `credibility` upsert, assert `credibility_history` row is not present
- [ ] Postgres migration file exists at `infra/postgres/migrations/` and creates `credibility_history` table with both indexes
- [ ] `scipy>=1.12` added to `src/services/credibility/requirements.txt`
- [ ] Integration test: a full `PredictionScored` message produces correct rows in both `credibility` and `credibility_history` tables
