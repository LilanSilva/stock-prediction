# T02: Source Credibility Tracker

## Context

This task extends the Credibility Service (`services/credibility/`) to track the credibility of individual news sources (domains like `di.se`, `svd.se`) independently from the causal graph edge weights updated in T01. The same `PredictionScored` message that drives edge updates also drives source credibility updates. Source scores are stored in Postgres and are consumed by the Dashboard's credibility trends chart and by future Prediction Service enhancements that weight source evidence.

## Background

### Why Track Source Credibility Separately?

Edge credibility (T01) tells us "how reliable is the `war->gold` causal relationship". Source credibility tells us "how reliable is `di.se` as a news source for market-moving events". These are orthogonal: a reliable source can correctly report on an unreliable causal relationship, and vice versa.

### Beta-Bernoulli Model for Sources

Identical model to T01:
- `alpha` (float): weighted count of correct predictions this source contributed to, initialised to 1.0
- `beta` (float): weighted count of incorrect predictions, initialised to 1.0
- `credibility_score` = `alpha / (alpha + beta)`

### Credit Assignment for Sources

Unlike edges where `influence_weight` is explicit, sources in `PredictionScored.sources[]` are a plain list of domain strings (e.g. `["di.se", "svd.se"]`). Credit is distributed **equally** across all sources in the list:

```
credit_per_source = 1.0 / len(sources)
```

On a **hit** (`is_correct = True`): `alpha_i += credit_per_source` for each source  
On a **miss** (`is_correct = False`): `beta_i += credit_per_source` for each source

### Message Schema

From `src/shared/schemas.py`, the `PredictionScored` fields relevant to this task:
- `prediction_id: str`
- `is_correct: bool`
- `sources: list[str]` — list of source domain strings (e.g. `["di.se", "dn.se"]`)
- `scored_at: datetime`

### Postgres Table

Source credibility is stored in the **same `credibility` table** introduced in T01, distinguished by `entity_type = 'source'`:

```sql
CREATE TABLE credibility (
  entity_id         TEXT NOT NULL,    -- edge_id OR source_domain
  entity_type       TEXT NOT NULL,    -- 'edge' OR 'source'
  alpha             FLOAT NOT NULL DEFAULT 1.0,
  beta              FLOAT NOT NULL DEFAULT 1.0,
  credibility_score FLOAT NOT NULL,
  last_updated      TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (entity_id, entity_type)
);
```

For a source row, `entity_id = 'di.se'` and `entity_type = 'source'`.

## Inputs

- **RabbitMQ queue:** `scored-predictions` (same consumer as T01 — this task adds source update logic to the same message handler)
- **Message type:** `PredictionScored` from `shared.schemas`
- **Postgres `credibility` table:** existing rows for sources (upsert on first encounter)
- **Environment variables:**
  - `RABBITMQ_URL`
  - `POSTGRES_DSN` (e.g. `postgresql+asyncpg://user:pass@postgres:5432/feedanalyzer`)

## Outputs

- **Postgres `credibility` table upserts:** one row per source domain per `PredictionScored` message. Columns updated: `alpha`, `beta`, `credibility_score`, `last_updated`.

## Technical Requirements

1. **Integration point:** add source update logic to `services/credibility/updater.py` alongside the edge update function. Both are called from the same `handle_prediction_scored` async function in `main.py`. The message is consumed once; both edge and source updates happen within the same message processing lifecycle.

2. **Source credit function** (in `services/credibility/updater.py`):
   ```python
   def compute_source_credits(
       sources: list[str]
   ) -> dict[str, float]:
       """
       Returns a dict mapping source_domain -> credit fraction.
       Uses equal weighting: 1.0 / len(sources).
       Returns empty dict if sources is empty.
       """
       ...
   ```

3. **Postgres upsert** for each source:
   ```sql
   INSERT INTO credibility (entity_id, entity_type, alpha, beta, credibility_score, last_updated)
   VALUES ($1, 'source', $2, $3, $4, NOW())
   ON CONFLICT (entity_id, entity_type)
   DO UPDATE SET
     alpha = credibility.alpha + EXCLUDED.alpha - 1.0,
     beta  = credibility.beta  + EXCLUDED.beta  - 1.0,
     credibility_score = (credibility.alpha + EXCLUDED.alpha - 1.0)
                         / ((credibility.alpha + EXCLUDED.alpha - 1.0)
                            + (credibility.beta + EXCLUDED.beta - 1.0)),
     last_updated = NOW();
   ```
   Note: the `- 1.0` subtractions in the `ON CONFLICT` branch remove the default prior (1.0) that was passed in `EXCLUDED` so we don't double-count it on updates. Alternatively, implement read-modify-write in Python and use a simple `UPDATE` for existing rows. Choose the approach that is easier to test.

4. **Preferred Python approach (simpler to test):** fetch current `alpha`/`beta` from Postgres first, apply the delta in Python, then upsert the final values. This avoids SQL arithmetic edge cases.
   ```python
   async def update_source_credibility(
       session: AsyncSession,
       source_domain: str,
       credit: float,
       is_correct: bool,
   ) -> tuple[float, float]:  # returns (new_alpha, new_beta)
       ...
   ```

5. **Alpha/beta floor:** enforce `max(value, 1.0)` on every write, identical to T01.

6. **Empty sources list handling:** if `PredictionScored.sources` is empty, log a WARNING (`"PredictionScored {prediction_id} has no sources — skipping source credibility update"`) and skip source updates without failing the message.

7. **Batch all source upserts** within a single Postgres transaction per message to ensure atomicity.

8. **Retry logic:** wrap Postgres writes with `tenacity` (same config as T01: exponential backoff, max 5 attempts).

9. **Logging:** log source domain, old alpha/beta, new alpha/beta at DEBUG level. Log count of sources updated at INFO level along with `prediction_id`.

## Acceptance Criteria

1. Given a `PredictionScored` message with `is_correct=True` and `sources=["di.se", "svd.se"]`, after processing, both `di.se` and `svd.se` have `alpha` incremented by exactly `0.5` (±0.0001) in the `credibility` table. Neither source's `beta` changes.

2. Given a `PredictionScored` message with `is_correct=False` and `sources=["aftonbladet.se"]`, `aftonbladet.se`'s `beta` increments by `1.0` (sole source, credit=1.0). `alpha` is unchanged.

3. A source encountered for the first time gets a new row with `alpha=1.0 + credit` (or `beta=1.0 + credit`), not just `credit`. The uninformed prior is the starting point.

4. `credibility_score` in the `credibility` table equals `alpha / (alpha + beta)` to 6 decimal places after each update.

5. A message with `sources=[]` logs a WARNING and processes successfully (message is `ack`-ed). No rows are written to `credibility` for sources.

6. A message with a single source assigns `credit = 1.0` to that source.

7. If Postgres is unavailable, the message is `nack`-ed with `requeue=True`. After Postgres recovers, the message is reprocessed correctly.

8. `entity_type = 'source'` for all rows written by this task; `entity_type = 'edge'` rows (from T01) are unaffected.

9. `pytest services/credibility/tests/test_source_credibility.py` passes.

10. `ruff check services/credibility/` and `mypy services/credibility/` both exit 0 (including the new source update code).

## Implementation Notes

- **Source domain normalisation:** normalise source domains to lowercase before using as `entity_id` (e.g. `"DI.SE"` → `"di.se"`). The upstream Ingestion Service should already normalise, but be defensive here.
- **`sources` field vs `contributing_edges`:** these are independent dimensions. A `PredictionScored` message always updates both. The code paths are separate functions called sequentially in `handle_prediction_scored`.
- **Idempotency:** same concern as T01 — if the message is delivered twice, both `alpha`/`beta` will double-count. The read-modify-write approach (fetch current values, apply delta, write back) is more idempotent-friendly if a `processed_predictions` guard is added later.
- **Credit when `len(sources) == 1`:** result is `1.0 / 1 = 1.0`. This is the full credit weight, which is correct — a lone source bears full responsibility.
- **Schema migration:** the `credibility` table is shared with T01 edge rows. If T01 migration already created the table, T02 does not need to re-create it. Ensure the Postgres migration script (`infra/postgres/migrations/`) creates the table with both use cases in mind.
- **Future use:** the `credibility_score` for sources will be used by the Prediction Service to weight conflicting news signals. Design the query interface with this in mind — the API Gateway will need to expose `GET /api/v1/credibility/sources` returning all source rows sorted by `credibility_score DESC`.

## Definition of Done

- [ ] Unit tests pass (`pytest services/credibility/tests/test_source_credibility.py`)
- [ ] `ruff check services/credibility/` exits 0
- [ ] `mypy services/credibility/` exits 0
- [ ] `compute_source_credits` is tested with: two sources, one source, empty list
- [ ] `update_source_credibility` is tested with: first-time source (new row), returning source (existing row), is_correct=True, is_correct=False
- [ ] Integration with T01: a single `PredictionScored` message triggers both edge and source updates in the correct order
- [ ] Postgres `credibility` table contains `entity_type='source'` rows after processing
- [ ] Source domain lowercasing is tested with mixed-case input
