# T03: Market Data & Events Endpoints

## Context

This task implements the remaining REST endpoints for the API Gateway: events (detected news events), prices (OHLC history), and health (service status aggregation). The events endpoints let the Dashboard display the news events that triggered predictions. The prices endpoint provides chart data. The health endpoint gives operators a single URL to check the status of the entire system.

## Background

The Cleansing Service writes `EventDetected` records to the `events` Postgres table. Each event may have multiple source articles (stored as a JSONB array of article IDs or URLs) and fact conflicts (also JSONB). The Ingestion Service writes raw articles to the `articles` table.

The Market Data Service writes `PriceObserved` records to the `prices` table after fetching OHLC from yfinance or Stooq.

The health endpoint is an aggregator: it makes HTTP GET requests to the `/health` endpoint of each of the six backend services and returns a combined status. This is used by the Dashboard header indicator and by external monitoring tools.

**Key Postgres tables:**
- `events` - columns: `event_id` (uuid), `canonical_summary` (text), `event_type` (str), `actor` (str), `action` (str), `object` (str), `entities` (jsonb array), `affected_assets` (jsonb array of strings), `first_seen` (timestamptz), `source_count` (int), `sources` (jsonb array), `fact_conflicts` (jsonb array), `correlation_id` (uuid), `created_at` (timestamptz)
- `articles` - columns: `article_id` (uuid), `source` (str), `url` (text), `title` (text), `body` (text), `published_at` (timestamptz), `language` (str), `country` (str)
- `prices` - columns: `price_id` (uuid), `asset` (str), `open` (float), `high` (float), `low` (float), `close` (float), `volume` (bigint), `observed_at` (timestamptz)

**Backend service health URLs (from env vars):**
- `INGESTION_HEALTH_URL` (e.g., `http://ingestion:8001/health`)
- `CLEANSING_HEALTH_URL` (e.g., `http://cleansing:8002/health`)
- `PREDICTION_HEALTH_URL` (e.g., `http://prediction:8003/health`)
- `MARKET_DATA_HEALTH_URL` (e.g., `http://market-data:8004/health`)
- `VERIFICATION_HEALTH_URL` (e.g., `http://verification:8005/health`)
- `CREDIBILITY_HEALTH_URL` (e.g., `http://credibility:8006/health`)

## Inputs

- Postgres connection pool (same pool as T01/T02, injected via `Depends(get_db)`)
- `httpx.AsyncClient` for health check HTTP calls (injected via `Depends(get_http_client)` or stored on `app.state`)
- Query parameters:
  - `GET /events`: `from_date` (ISO date), `to_date` (ISO date), `event_type` (str), `asset` (str, filters on `affected_assets` JSONB array), `limit` (int default 50 max 200), `offset` (int default 0)
  - `GET /events/{id}`: path param `id` (UUID)
  - `GET /prices/{symbol}`: path param `symbol` (str), optional `from_date` (ISO date), `to_date` (ISO date), `limit` (int default 500)
  - `GET /health`: no parameters

## Outputs

**GET /events** returns:
```json
{
  "total": 88,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "event_id": "uuid",
      "canonical_summary": "Russian forces advance on Kyiv",
      "event_type": "CONFLICT",
      "actor": "Russia",
      "action": "military_advance",
      "object": "Kyiv",
      "affected_assets": ["GOLD", "OIL"],
      "first_seen": "2025-01-15T08:00:00Z",
      "source_count": 4,
      "created_at": "2025-01-15T08:05:00Z"
    }
  ]
}
```

**GET /events/{id}** returns full detail including `entities`, `sources` (list of article objects with `url`, `title`, `source`, `published_at`), `fact_conflicts`.

**GET /prices/{symbol}** returns:
```json
{
  "symbol": "GOLD",
  "items": [
    {"observed_at": "2025-01-15T16:00:00Z", "open": 2650.10, "high": 2658.40, "low": 2645.00, "close": 2655.80, "volume": 182400}
  ]
}
```

**GET /health** returns:
```json
{
  "status": "ok",
  "services": {
    "ingestion": {"status": "ok", "latency_ms": 12},
    "cleansing": {"status": "ok", "latency_ms": 8},
    "prediction": {"status": "degraded", "error": "connection refused"},
    "market_data": {"status": "ok", "latency_ms": 15},
    "verification": {"status": "ok", "latency_ms": 9},
    "credibility": {"status": "ok", "latency_ms": 11}
  }
}
```
Top-level `status` is `"ok"` only if ALL services are `"ok"`. Otherwise `"degraded"`.

## Technical Requirements

1. **Router files:** `src/services/api-gateway/routers/events.py`, `src/services/api-gateway/routers/prices.py`, `src/services/api-gateway/routers/health.py`
2. **JSONB array filter for asset:** Use Postgres operator `@>` to filter events where `affected_assets` contains the given asset string: `WHERE affected_assets @> $1::jsonb` where `$1 = json.dumps([asset])`.
3. **Health checks:** Use `httpx.AsyncClient` with a timeout of `2.0` seconds per service. Run all six health checks concurrently with `asyncio.gather(*checks, return_exceptions=True)`. A timeout or connection error sets that service's status to `"degraded"` with an `error` field.
4. **Health check HTTP client:** Create a single `httpx.AsyncClient` instance at application startup (in the FastAPI `lifespan`) and store on `app.state.http_client`. Close it on shutdown. Do NOT create a new client per request.
5. **Prices ordering:** Return price rows sorted by `observed_at ASC`.
6. **Events detail - source articles:** The `events.sources` JSONB column contains article IDs. For the detail endpoint, look up each article ID in the `articles` table and return the article metadata (url, title, source, published_at). Use `WHERE article_id = ANY($1::uuid[])` for the batch lookup.
7. **Response schemas:** Define in `src/services/api-gateway/schemas/events.py` and `src/services/api-gateway/schemas/health.py`.
8. **Health endpoint caching:** Cache the health response for 10 seconds to prevent hammering downstream services if the Dashboard polls frequently. Use a simple in-memory dict `{"cached_at": datetime, "result": dict}` on `app.state`. No Redis required.
9. **prices/{symbol} with no data:** Return `{"symbol": "GOLD", "items": []}` (not 404).
10. **events/{id} not found:** Return HTTP 404 with `{"detail": "Event {id} not found"}`.

## Acceptance Criteria

1. `GET /events` returns paginated JSON with `total`, `limit`, `offset`, `items`
2. `GET /events?asset=GOLD` returns only events where `GOLD` appears in `affected_assets`
3. `GET /events?event_type=CONFLICT` returns only events with `event_type == "CONFLICT"`
4. `GET /events?from_date=2025-01-01&to_date=2025-01-31` filters by `first_seen` date range
5. `GET /events/{valid-uuid}` returns full detail including `sources` as a list of article objects (not just IDs)
6. `GET /events/{unknown-uuid}` returns HTTP 404
7. `GET /prices/GOLD` returns `{"symbol": "GOLD", "items": [...]}` sorted by `observed_at` ascending
8. `GET /prices/UNKNOWN` returns `{"symbol": "UNKNOWN", "items": []}` (not 404)
9. `GET /health` returns `"status": "ok"` when all six services respond with HTTP 200 within 2 seconds
10. `GET /health` returns `"status": "degraded"` when any service is unreachable
11. `GET /health` includes per-service `latency_ms` for healthy services and `error` string for unreachable ones
12. Health checks for all six services run concurrently (not sequentially)
13. Health endpoint returns a cached response within 10 seconds of the last real check (no duplicate requests to backends within the cache window)
14. All tests in `src/services/api-gateway/tests/test_events.py` and `src/services/api-gateway/tests/test_health.py` pass

## Implementation Notes

- The JSONB `@>` containment operator for filtering events by asset: the right-hand side must be a JSON array, not a plain string. Pass `json.dumps([asset_name])` as the parameter value, e.g. `WHERE affected_assets @> $1::jsonb` with param `'["GOLD"]'`.
- For the health endpoint, use `asyncio.gather` with `return_exceptions=True`. The result list may contain `Exception` instances for failed checks - handle these by setting `status = "degraded"` and extracting `str(exc)` as the error message.
- Health check latency: record `time.monotonic()` before and after each `client.get()` call and compute `latency_ms = (end - start) * 1000`.
- The 10-second health cache prevents the Dashboard's polling (if it polls every 5 seconds) from creating a thundering herd. Implement as: check `app.state.health_cache_at`; if `None` or `(now - cached_at).total_seconds() > 10`, run fresh checks and update the cache.
- The `GET /events/{id}` source article enrichment: the `events.sources` column stores a list of objects like `[{"article_id": "uuid", "url": "..."}]`. Extract the `article_id` values, batch-fetch from `articles`, then merge the full article data back into the response. If an article ID is missing from `articles` (e.g., deleted), include the raw source object without enrichment.
- `GET /prices/{symbol}` - the `symbol` path parameter should be uppercased before querying: `symbol = symbol.upper()`. Swedish tickers like `ERIC-B.ST` contain hyphens and dots - these are valid URL path characters, no special handling needed.

## Definition of Done

- [ ] Unit tests pass (`pytest src/services/api-gateway/tests/test_events.py src/services/api-gateway/tests/test_health.py`)
- [ ] Code passes `ruff check src/services/api-gateway/` with zero errors
- [ ] Code passes `mypy src/services/api-gateway/ --strict` with zero errors
- [ ] All 14 acceptance criteria verified
- [ ] Health checks are concurrent via `asyncio.gather`
- [ ] Health response cached for 10 seconds (verified by test asserting only one outbound HTTP call within cache window)
- [ ] `httpx.AsyncClient` created once at startup, closed at shutdown
- [ ] JSONB asset filter uses `@>` containment operator with correct JSON array encoding
