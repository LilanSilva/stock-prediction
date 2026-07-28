# T01: Predictions Endpoints

## Context

The API Gateway is a FastAPI BFF that reads from Postgres and serves the React Dashboard. This task implements the three prediction-related REST endpoints that allow the Dashboard to display the full predictions table, drill into a single prediction, and show accuracy statistics. These are the highest-traffic endpoints; every Dashboard page load hits `/predictions`.

## Background

The Prediction Service writes `PredictionMade` records to Postgres when it processes an event. The Verification Service later writes a matching `prediction_outcomes` row (or updates the prediction row) when the time window closes. A prediction is `PENDING` until its `window_close_at` timestamp passes; after that it is `SCORED` with an `is_correct` boolean.

The endpoints must join `predictions` with `prediction_outcomes` so the Dashboard can show both the original prediction and its outcome in one response object. The `contributing_edges` and `sources` fields are stored as JSONB arrays in Postgres.

**Key Postgres tables:**
- `predictions` - one row per `PredictionMade` message
- `prediction_outcomes` - one row per `PredictionScored` message, FK to `predictions.prediction_id`
- `events` - referenced by `predictions.event_ids[]` (array FK)

**Shared Pydantic schemas (from `src/shared/schemas.py`):**
- `PredictionMade` - source of truth for prediction field names
- `PredictionScored` - source of truth for outcome field names

## Inputs

- Postgres connection pool (asyncpg via SQLAlchemy async or `databases` library)
- Query parameters from HTTP clients:
  - `GET /predictions`: `asset` (str), `direction` (UP/DOWN/NEUTRAL), `status` (PENDING/SCORED), `is_correct` (bool), `from_date` (ISO date), `to_date` (ISO date), `limit` (int, default 50, max 200), `offset` (int, default 0)
  - `GET /predictions/{id}`: path param `id` (UUID string)
  - `GET /predictions/stats`: optional `asset` (str), `from_date`, `to_date`

## Outputs

**GET /predictions** returns:
```json
{
  "total": 142,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "prediction_id": "uuid",
      "asset": "GOLD",
      "direction": "UP",
      "magnitude_bucket": "SMALL",
      "confidence": 0.72,
      "time_horizon": "24h",
      "created_at": "2025-01-15T10:30:00Z",
      "window_close_at": "2025-01-16T10:30:00Z",
      "status": "SCORED",
      "is_correct": true,
      "actual_direction": "UP",
      "scored_at": "2025-01-16T11:00:00Z"
    }
  ]
}
```

**GET /predictions/{id}** returns full detail including `rationale` (str), `contributing_edges` (list of edge objects), `sources` (list of source strings), and `event_ids` (list of UUIDs).

**GET /predictions/stats** returns:
```json
{
  "total": 142,
  "correct": 89,
  "wrong": 43,
  "pending": 10,
  "accuracy_rate": 0.674,
  "by_asset": [
    {"asset": "GOLD", "total": 30, "correct": 21, "accuracy_rate": 0.70}
  ]
}
```

## Technical Requirements

1. **Framework:** FastAPI `APIRouter` mounted at prefix `/predictions` in `src/services/api-gateway/routers/predictions.py`
2. **Database access:** Use `asyncpg` directly or `SQLAlchemy 2.x` async engine with connection pool size 10. Connection string from env var `DATABASE_URL` (e.g., `postgresql+asyncpg://user:pass@postgres:5432/feedanalyzer`).
3. **ORM vs raw SQL:** Prefer raw SQL with `asyncpg` for performance. Use parameterized queries only - no f-string interpolation of user inputs.
4. **Pagination:** `limit` must be capped at 200. Return HTTP 400 if `limit < 1` or `offset < 0`.
5. **Date filter:** `from_date` and `to_date` are `date` strings (YYYY-MM-DD). Filter on `predictions.created_at::date`.
6. **Status derivation:** If no matching `prediction_outcomes` row exists AND `window_close_at > NOW()`, status = `PENDING`. If no outcome AND `window_close_at <= NOW()`, status = `OVERDUE`. If outcome exists, status = `SCORED`.
7. **stats endpoint ordering:** Route `/predictions/stats` must be registered BEFORE `/predictions/{id}` in the router to avoid FastAPI matching `stats` as a path parameter.
8. **Response models:** Define Pydantic response models in `src/services/api-gateway/schemas/predictions.py`. Do not reuse the `src/shared/` message schemas directly as response models - shape them for the API consumer.
9. **Error handling:** Return `HTTP 404` with `{"detail": "Prediction {id} not found"}` for unknown IDs. Return `HTTP 422` automatically via FastAPI for invalid query params.
10. **Logging:** Use `structlog` (already in shared requirements) to log each request with `prediction_id` or filters as structured fields.

## Acceptance Criteria

1. `GET /predictions` returns a JSON object with `total`, `limit`, `offset`, and `items` fields
2. `GET /predictions?limit=5&offset=10` returns at most 5 items and reflects the correct `offset` in the response
3. `GET /predictions?asset=GOLD` returns only predictions where `asset == "GOLD"`
4. `GET /predictions?status=PENDING` returns only predictions with no scored outcome and `window_close_at > NOW()`
5. `GET /predictions?is_correct=true` returns only predictions where the outcome has `is_correct = true`
6. `GET /predictions?from_date=2025-01-01&to_date=2025-01-31` filters by `created_at` date range inclusive
7. `GET /predictions/{valid-uuid}` returns a response containing `rationale`, `contributing_edges`, `sources`, and `event_ids` fields
8. `GET /predictions/{unknown-uuid}` returns HTTP 404
9. `GET /predictions/stats` returns `total`, `correct`, `wrong`, `pending`, `accuracy_rate`, and `by_asset` fields
10. `GET /predictions/stats?asset=GOLD` returns stats scoped to GOLD only with correct counts
11. `accuracy_rate` in stats equals `correct / (correct + wrong)` and is 0 when both are 0
12. All endpoints respond within 200 ms on a local Docker setup with 1000 seeded rows
13. `GET /predictions?limit=201` returns HTTP 400
14. All tests in `src/services/api-gateway/tests/test_predictions.py` pass

## Implementation Notes

- The join query for the list endpoint: `SELECT p.*, po.actual_direction, po.is_correct, po.scored_at FROM predictions p LEFT JOIN prediction_outcomes po ON p.prediction_id = po.prediction_id WHERE ... ORDER BY p.created_at DESC LIMIT $1 OFFSET $2`
- For the `total` count in paginated responses, run a separate `SELECT COUNT(*)` with the same WHERE clause. Do NOT use `COUNT(*) OVER()` window function unless you benchmark it - it can be slower on large tables.
- `contributing_edges` and `sources` are stored as `jsonb` in Postgres. `asyncpg` returns them as Python `str` - call `json.loads()` on them before including in the response.
- The `/predictions/stats` `accuracy_rate` must exclude `PENDING` and `OVERDUE` predictions from the denominator (only count rows with a scored outcome).
- Index hint: ensure `predictions(asset, created_at)` and `predictions(created_at)` composite indexes exist (migration task should create them, but add a comment in code if missing).
- FastAPI path parameter routes and fixed-string routes at the same level: always register `/stats` before `/{id}` to prevent shadowing.

## Definition of Done

- [ ] Unit tests pass (`pytest src/services/api-gateway/tests/test_predictions.py`)
- [ ] Code passes `ruff check src/services/api-gateway/` with zero errors
- [ ] Code passes `mypy src/services/api-gateway/ --strict` with zero errors
- [ ] All 14 acceptance criteria verified manually or via automated tests
- [ ] No raw f-string SQL interpolation - all queries use parameterized `$1`, `$2` placeholders
- [ ] Response schemas documented with FastAPI's OpenAPI auto-generation (viewable at `/docs`)
- [ ] `GET /predictions/stats` route registered before `GET /predictions/{id}` in router
