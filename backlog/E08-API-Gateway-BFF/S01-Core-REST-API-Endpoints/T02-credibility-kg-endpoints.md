# T02: Credibility & Knowledge Graph Endpoints

## Context

The API Gateway serves the Dashboard's credibility and knowledge-graph views. This task implements four endpoints that expose the current state of the Bayesian credibility weights stored in Postgres and the causal structure of the Neo4j knowledge graph. The Credibility Service writes updated alpha/beta weights to Postgres after each scored prediction; the Dashboard reads them here to render trend charts and edge rankings.

## Background

The system uses a Beta-Bernoulli Bayesian model to track the credibility of each causal edge in the knowledge graph. Each edge has:
- `alpha`: count of correct predictions that used this edge (prior starts at 1)
- `beta`: count of incorrect predictions (prior starts at 1)
- `credibility`: the mean of the Beta distribution = `alpha / (alpha + beta)`
- `confidence_interval`: 95% HDI of the Beta distribution

Sources (GDELT, DI, DN, Aftonbladet, SvD) also have their own alpha/beta tracked in `credibility_sources`.

The `/graph/assets/{symbol}` endpoint is the only one that reads from **Neo4j** rather than Postgres. It retrieves all causal edges that point to or from a given asset symbol (e.g., `GOLD`, `OIL`) using a Cypher query, then enriches each edge with the current alpha/beta from Postgres.

**Key Postgres tables:**
- `credibility_edges` - columns: `edge_id` (str, e.g. `war->gold`), `cause` (str), `effect` (str), `sign` (+/-), `alpha` (float), `beta` (float), `credibility` (float), `confidence_interval_low` (float), `confidence_interval_high` (float), `updated_at` (timestamptz)
- `credibility_sources` - columns: `source_id` (str), `source_name` (str), `alpha` (float), `beta` (float), `credibility` (float), `updated_at` (timestamptz)
- `credibility_edge_history` - columns: `id` (serial), `edge_id` (str FK), `alpha` (float), `beta` (float), `credibility` (float), `recorded_at` (timestamptz)

**Neo4j:**
- Nodes: `(:Asset {symbol: "GOLD"})`, `(:Concept {name: "war"})`
- Edges: `[:CAUSES {edge_id: "war->gold", sign: "+", weight: 0.7}]`

**Shared library:** `src/shared/neo4j_client.py` - async Neo4j driver wrapper using `neo4j` Python driver

## Inputs

- Postgres connection pool (same pool as T01, injected via FastAPI dependency)
- Neo4j async driver (from `src/shared/neo4j_client.py`, connection from env vars `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`)
- Query parameters:
  - `GET /credibility/edges`: optional `min_credibility` (float 0-1), `limit` (int default 100), `offset` (int default 0)
  - `GET /credibility/sources`: no parameters
  - `GET /credibility/history/{edge_id}`: path param `edge_id` (str, e.g. `war->gold`), optional `limit` (int default 90)
  - `GET /graph/assets/{symbol}`: path param `symbol` (str, e.g. `GOLD`)

## Outputs

**GET /credibility/edges** returns:
```json
{
  "total": 47,
  "items": [
    {
      "edge_id": "war->gold",
      "cause": "war",
      "effect": "gold",
      "sign": "+",
      "alpha": 14.0,
      "beta": 6.0,
      "credibility": 0.7,
      "confidence_interval_low": 0.47,
      "confidence_interval_high": 0.88,
      "updated_at": "2025-01-15T12:00:00Z"
    }
  ]
}
```

**GET /credibility/sources** returns a list sorted by `credibility` descending:
```json
[
  {"source_id": "di", "source_name": "Dagens Industri", "alpha": 22.0, "beta": 8.0, "credibility": 0.733, "updated_at": "..."}
]
```

**GET /credibility/history/{edge_id}** returns time-series sorted by `recorded_at` ascending:
```json
[
  {"recorded_at": "2025-01-10T00:00:00Z", "alpha": 5.0, "beta": 3.0, "credibility": 0.625}
]
```

**GET /graph/assets/{symbol}** returns all causal edges touching the asset:
```json
{
  "symbol": "GOLD",
  "edges": [
    {
      "edge_id": "war->gold",
      "cause": "war",
      "effect": "GOLD",
      "sign": "+",
      "neo4j_weight": 0.7,
      "credibility": 0.70,
      "alpha": 14.0,
      "beta": 6.0
    }
  ]
}
```

## Technical Requirements

1. **Router file:** `services/api-gateway/routers/credibility.py` for credibility endpoints; `services/api-gateway/routers/graph.py` for graph endpoints
2. **Postgres access:** Same async connection pool as T01. Inject via `Depends(get_db)` FastAPI dependency defined in `services/api-gateway/dependencies.py`
3. **Neo4j access:** Use the `neo4j` Python async driver (`neo4j>=5.0`). Inject async session via `Depends(get_neo4j_session)` FastAPI dependency. Connection params from env vars: `NEO4J_URI` (e.g., `bolt://neo4j:7687`), `NEO4J_USER`, `NEO4J_PASSWORD`
4. **Cypher query for graph endpoint:**
   ```cypher
   MATCH (n)-[r:CAUSES]->(m)
   WHERE n.symbol = $symbol OR m.symbol = $symbol
   RETURN n.name AS cause, m.symbol AS effect, r.edge_id AS edge_id, r.sign AS sign, r.weight AS neo4j_weight
   ```
   After fetching Neo4j results, query Postgres for matching `edge_id` values to enrich with `alpha`, `beta`, `credibility`.
5. **min_credibility filter:** `WHERE credibility >= $1` in the edges query. Default: no filter (return all).
6. **edge_id URL encoding:** `edge_id` values like `war->gold` contain `>` which is valid in path segments but must be documented. Test with URL-encoded form `war-%3Egold` as well.
7. **Response schemas:** Define in `services/api-gateway/schemas/credibility.py`. Use Pydantic v2 `model_config = ConfigDict(from_attributes=True)`.
8. **Sorting:** `/credibility/edges` sorts by `credibility DESC`. `/credibility/sources` sorts by `credibility DESC`. `/credibility/history/{edge_id}` sorts by `recorded_at ASC`.
9. **Missing Postgres row for Neo4j edge:** If a Neo4j edge has no matching row in `credibility_edges`, return `alpha=1.0`, `beta=1.0`, `credibility=0.5` (uninformed prior defaults).
10. **Logging:** Log Neo4j query latency as structured field `neo4j_query_ms`.

## Acceptance Criteria

1. `GET /credibility/edges` returns a JSON object with `total` and `items` fields
2. `GET /credibility/edges?min_credibility=0.7` returns only edges where `credibility >= 0.7`
3. `GET /credibility/sources` returns a list sorted by `credibility` descending
4. `GET /credibility/history/war->gold` returns a list of time-series points sorted by `recorded_at` ascending
5. `GET /credibility/history/nonexistent-edge` returns an empty list (not 404)
6. `GET /graph/assets/GOLD` returns an object with `symbol` and `edges` fields
7. `GET /graph/assets/GOLD` enriches each Neo4j edge with `credibility`, `alpha`, `beta` from Postgres
8. `GET /graph/assets/GOLD` returns `credibility=0.5` for any edge not found in `credibility_edges` Postgres table
9. `GET /graph/assets/UNKNOWN_SYMBOL` returns `{"symbol": "UNKNOWN_SYMBOL", "edges": []}` (not 404)
10. Neo4j connection failure causes a `503 Service Unavailable` response, not an unhandled 500
11. All tests in `services/api-gateway/tests/test_credibility.py` pass

## Implementation Notes

- Neo4j driver initialization should happen once at application startup via FastAPI `lifespan` context manager, not per-request. Store the driver on `app.state.neo4j_driver`.
- The `neo4j` async driver requires `await session.run(...)` to return a `Result` object; call `await result.data()` to get the list of records as dicts.
- `edge_id` values may contain special characters. When used as path parameters FastAPI decodes them automatically, but write a test with `%3E` encoding to confirm round-trip correctness.
- The credibility history endpoint with `limit=90` is designed to support a 90-day rolling chart in the Dashboard. If the history table has fewer than `limit` rows for an edge, return all available rows.
- Consider adding a `GET /credibility/edges/{edge_id}` endpoint for the Dashboard's edge detail panel even if not in the spec - check with the Dashboard team before adding.
- For the Postgres enrichment step in the graph endpoint, use `WHERE edge_id = ANY($1::text[])` to batch-fetch all edges in one query rather than N+1 individual queries.

## Definition of Done

- [ ] Unit tests pass (`pytest services/api-gateway/tests/test_credibility.py`)
- [ ] Code passes `ruff check services/api-gateway/` with zero errors
- [ ] Code passes `mypy services/api-gateway/ --strict` with zero errors
- [ ] All 11 acceptance criteria verified
- [ ] Neo4j driver initialized once at startup, not per-request
- [ ] Batch Postgres lookup used in graph endpoint (no N+1 queries)
- [ ] Neo4j errors return HTTP 503, not HTTP 500
- [ ] Response schemas in `services/api-gateway/schemas/credibility.py`
