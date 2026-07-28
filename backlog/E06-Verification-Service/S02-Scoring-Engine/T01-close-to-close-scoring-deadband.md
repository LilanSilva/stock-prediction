# T01: Close-to-close scoring with deadband

## Context

This task implements the core scoring algorithm inside the Verification Service (`src/services/verification/`). It is called by the window-close APScheduler job (built in S01/T01) after confirming that a `PriceObservation` row exists for the prediction. The result — actual return, actual direction, is_correct, magnitude accuracy — is written back to the `outcomes` Postgres table. The publisher (S02/T02) then reads these columns to build the outbound message.

## Background

**Close-to-close return:** The baseline price is the asset's close at the time the prediction was created (`created_at`). The comparison price is the asset's close at `window_close_at`. Both prices come from the `price_observations` table.

The Market Data Service stores two price rows per prediction:
1. The baseline close: `observed_at` approximately equal to `outcomes.created_at` — identified by matching `prediction_id` and the earlier timestamp.
2. The window close: `observed_at` approximately equal to `outcomes.window_close_at`.

If only one price row exists (the window-close price), treat the prediction's `created_at` close as unavailable and use the single available close as both numerator reference. In V1, the Market Data Service is expected to provide both prices in one `PriceObserved` message (the message carries `open` = open at creation, `close` = close at window). Treat `open` as baseline and `close` as the window close price.

**Return formula:**
```
actual_return = (close - open) / open
```
Where `open` = `price_observations.open` (price at prediction time) and `close` = `price_observations.close` (price at window close).

**Deadband rule:**
```
if abs(actual_return) < 0.003:   # ±0.3%
    actual_direction = "NEUTRAL"
elif actual_return > 0:
    actual_direction = "UP"
else:
    actual_direction = "DOWN"
```

**Correctness:**
```
is_correct = (predicted_direction == actual_direction)
```

**Magnitude bucket scoring:**
The `predicted_magnitude` column holds a bucket label (e.g. `"small"`, `"medium"`, `"large"`). Compute `actual_magnitude_bucket` using the same bucket boundaries:
- `|actual_return| < 0.005` → `"small"`
- `0.005 <= |actual_return| < 0.020` → `"medium"`
- `|actual_return| >= 0.020` → `"large"`

Store `magnitude_correct = (predicted_magnitude == actual_magnitude_bucket)` in a JSONB extras field or as a separate column (add `magnitude_correct BOOLEAN` column to the `outcomes` DDL if not present).

**Score field:** Set `score = 1.0` if `is_correct`, `0.0` otherwise. Future iterations may use a continuous score.

## Inputs

- **Database read:** `outcomes` row identified by `prediction_id` — provides `predicted_direction`, `predicted_magnitude`, `created_at`, `window_close_at`.
- **Database read:** `price_observations` row(s) matching `prediction_id` — provides `open`, `close`, `observed_at`.

No queue consumption in this task — it is invoked as a function by the scheduler job.

## Outputs

- **Database update:** `outcomes` row updated with:
  - `actual_direction` (str: UP | DOWN | NEUTRAL)
  - `actual_return` (float)
  - `is_correct` (bool)
  - `score` (float: 1.0 or 0.0)
  - `magnitude_correct` (bool)
  - `status = "SCORED"`
  - `scored_at = datetime.now(tz=timezone.utc)`
- **Return value:** A `ScoringResult` dataclass (defined in this module) used by S02/T02 to build the outbound message.

## Technical Requirements

### 1. Module location

Create `src/services/verification/app/scoring.py`.

### 2. ScoringResult dataclass

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ScoringResult:
    prediction_id: str
    asset: str
    predicted_direction: str
    actual_direction: str
    predicted_magnitude: str
    actual_magnitude: str
    actual_return: float
    is_correct: bool
    magnitude_correct: bool
    score: float
    contributing_edges: list[dict]
    sources: list[str]
    scored_at: datetime
```

### 3. Main scoring function signature

```python
async def score_prediction(
    prediction_id: str,
    session: AsyncSession,
) -> ScoringResult | None:
    """
    Load outcome and price observation from DB, compute score, persist, return ScoringResult.
    Returns None if data is incomplete (logs warning).
    """
```

### 4. Deadband constant

Define at module level:
```python
DEADBAND_THRESHOLD: float = 0.003  # ±0.3%
```

Do not hard-code `0.003` inline — always reference the constant so tests can override it.

### 5. Magnitude bucket function

```python
def classify_magnitude(actual_return: float) -> str:
    abs_ret = abs(actual_return)
    if abs_ret < 0.005:
        return "small"
    elif abs_ret < 0.020:
        return "medium"
    else:
        return "large"
```

### 6. Outcomes DDL amendment

If `magnitude_correct` column does not exist, add to `infra/postgres/init/03_verification.sql`:
```sql
ALTER TABLE outcomes ADD COLUMN IF NOT EXISTS magnitude_correct BOOLEAN;
```

Or include it in the original `CREATE TABLE` if S01 DDL has not been applied yet.

### 7. Price observation selection logic

When querying `price_observations` for a given `prediction_id`:
1. Fetch all rows matching `prediction_id` ordered by `observed_at ASC`.
2. If zero rows: return `None`, log warning `"No price data for prediction_id=%s"`.
3. If one row: use `row.open` as baseline, `row.close` as window close.
4. If multiple rows: use the earliest row's `open` as baseline and the latest row's `close` as window close.

### 8. Update the outcomes row

Use `sqlalchemy` `update()` statement or fetch-and-mutate pattern with `AsyncSession`. Ensure the session is committed before returning.

### 9. Scheduler integration

In `app/scheduler.py` (from S01/T01), the `on_window_close` job function must import and call `score_prediction`:
```python
from app.scoring import score_prediction

async def on_window_close(prediction_id: str) -> None:
    async with get_session() as session:
        price_obs = await get_price_observation(prediction_id, session)
        if price_obs is None:
            await publish_price_request(prediction_id, session)
            return
        result = await score_prediction(prediction_id, session)
        if result:
            await publish_prediction_scored(result)  # implemented in S02/T02
```

### 10. Libraries

No new dependencies beyond what S01 already requires. Uses:
- `sqlalchemy[asyncio]>=2.0`
- `asyncpg>=0.29`
- Python standard library `dataclasses`, `datetime`

## Acceptance Criteria

1. Given `open = 100.0` and `close = 100.5`, `actual_return = 0.005`, `actual_direction = "UP"`.
2. Given `open = 100.0` and `close = 100.2`, `actual_return = 0.002`, `actual_direction = "NEUTRAL"` (within deadband).
3. Given `open = 100.0` and `close = 99.5`, `actual_return = -0.005`, `actual_direction = "DOWN"`.
4. `is_correct = True` when `predicted_direction == actual_direction`.
5. `is_correct = False` when `predicted_direction != actual_direction` (including NEUTRAL mismatch).
6. `classify_magnitude(0.002)` returns `"small"`, `classify_magnitude(0.010)` returns `"medium"`, `classify_magnitude(0.025)` returns `"large"`.
7. After `score_prediction` completes, the `outcomes` table row has `status = "SCORED"`, `scored_at` is populated, and all score columns are non-null.
8. If no `PriceObservation` row exists, `score_prediction` returns `None` and the `outcomes` row is not modified.
9. Unit tests in `tests/test_scoring.py` cover all deadband boundary cases (at boundary, inside, outside) and all direction combinations.
10. `ruff check` and `mypy` report zero errors on `app/scoring.py`.

## Implementation Notes

- **Deadband boundary:** `abs(actual_return) < 0.003` is a strict less-than. A return of exactly `0.003` is classified as `UP` or `DOWN`, not `NEUTRAL`. Make sure the unit test covers this exact boundary.
- **Float precision:** Avoid comparing floats with `==` in the deadband check. Use `math.isclose` only for test assertions, not for production logic (the formula is deterministic).
- **Negative open price:** Not expected for assets in scope, but guard against division by zero: if `open == 0.0`, return `None` and log an error.
- **NEUTRAL prediction correctness:** If `predicted_direction == "NEUTRAL"` and `actual_direction == "NEUTRAL"`, `is_correct = True`. This case is valid and must be tested.
- **Session ownership:** The `score_prediction` function accepts an `AsyncSession` parameter rather than creating its own, so the caller (the scheduler job) controls the transaction boundary. This makes unit testing with a mock session straightforward.
- **Idempotency:** If the `outcomes` row already has `status = "SCORED"`, skip re-scoring and return the existing data as a `ScoringResult`. Log at INFO level.

## Definition of Done

- [ ] `src/services/verification/app/scoring.py` exists and is fully type-annotated
- [ ] `DEADBAND_THRESHOLD` constant defined at module level
- [ ] `classify_magnitude()` function implemented and unit tested
- [ ] `ScoringResult` dataclass defined with all fields
- [ ] `score_prediction()` async function implemented
- [ ] `outcomes` DDL includes `magnitude_correct` column
- [ ] `tests/test_scoring.py` exists with tests for all 9 acceptance criteria
- [ ] `ruff check src/services/verification/app/scoring.py` exits 0
- [ ] `mypy src/services/verification/app/scoring.py` exits 0
- [ ] Scheduler `on_window_close` calls `score_prediction` and passes result to publisher stub
