# T01: Knowledge Graph Schema & Cypher Queries

## Context

The Prediction Service (located at `services/prediction/`) relies on a Neo4j knowledge graph to find causal edges that "fire" when a given event type and set of entities are observed. This task defines the graph schema, creates the necessary indexes, and implements the three Cypher query functions that the Prediction Engine calls at runtime. This is a foundational task: nothing in S02 can be built or tested without this in place.

## Background

The knowledge graph uses a property graph model with two node types and one relationship type:

- **`Event` node**: represents a category of real-world events (e.g. `military_conflict`, `rate_hike`). Has properties `type` (string, unique identifier), `keywords` (list of strings used for fuzzy matching).
- **`Asset` node**: represents a tradeable asset. Has properties `symbol` (string, e.g. `GC=F` for gold futures), `name` (human-readable), `asset_class` (one of `COMMODITY`, `CURRENCY`, `EQUITY_INDEX`, `EQUITY`).
- **`AFFECTS` relationship**: directed edge from `Event` to `Asset`. Properties:
  - `direction`: `UP` or `DOWN` (string)
  - `base_weight`: float, the original expert-assigned weight (0.0–1.0), never mutated
  - `current_weight`: float, the live Bayesian-updated weight (starts equal to `base_weight`)
  - `alpha`: int, Beta-Bernoulli alpha count (correct predictions), starts at 1
  - `beta`: int, Beta-Bernoulli beta count (incorrect predictions), starts at 1
  - `last_updated`: ISO-8601 datetime string

At runtime, the Prediction Engine calls three functions:
1. `match_event_to_nodes(event_type, entities)` - finds Event nodes whose `type` matches `event_type` or whose `keywords` overlap with `entities`.
2. `get_firing_subgraph(event_type, entities)` - returns all AFFECTS edges reachable from matching Event nodes, including multi-hop (where an intermediate Asset is itself the source of another AFFECTS edge).
3. `update_edge_weight(source_type, asset_symbol, direction, new_alpha, new_beta)` - called by the Credibility Service after scoring.

The graph driver uses the `neo4j` Python async driver (`neo4j>=5.0`, `pip install neo4j`).

## Inputs

- Neo4j connection URI, username, and password (from environment variables: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`)
- At query time: `event_type: str`, `entities: list[str]` from an `EventDetected` message

## Outputs

- Python module at `services/prediction/graph/neo4j_client.py` implementing `GraphMatcher` class
- Cypher schema/index setup script at `infra/neo4j/schema.cypher`
- Unit tests at `services/prediction/tests/test_neo4j_client.py`

## Technical Requirements

### File: `services/prediction/graph/neo4j_client.py`

```
from neo4j import AsyncGraphDatabase, AsyncDriver
```

Implement `GraphMatcher` class with:

**`__init__(self, uri: str, user: str, password: str)`**
- Creates `AsyncGraphDatabase.driver(uri, auth=(user, password))`
- Stores driver as `self._driver`

**`async def close(self) -> None`**
- Calls `await self._driver.close()`

**`async def match_event_to_nodes(self, event_type: str, entities: list[str]) -> list[str]`**
- Returns matched Event node `type` values
- Cypher: match on `e.type = $event_type OR ANY(k IN e.keywords WHERE k IN $entities)`
- Returns list of matching event type strings

**`async def get_firing_subgraph(self, event_type: str, entities: list[str]) -> list[FiringEdge]`**
- Returns all AFFECTS edges from matched Event nodes
- `FiringEdge` is a Pydantic `BaseModel` with fields: `source_event_type: str`, `asset_symbol: str`, `asset_name: str`, `direction: str`, `current_weight: float`, `base_weight: float`, `alpha: int`, `beta: int`
- Query must also traverse **one level of multi-hop**: if Asset A is both a target of an Event edge AND the source of another AFFECTS edge (transitively), include that second-level edge with direction and weight
- Group results by `asset_symbol` before returning (do not deduplicate - return all edges per asset)

**`async def update_edge_weight(self, source_event_type: str, asset_symbol: str, direction: str, new_alpha: int, new_beta: int) -> None`**
- Cypher: MATCH `(e:Event {type: $event_type})-[r:AFFECTS {direction: $direction}]->(a:Asset {symbol: $asset_symbol})` SET `r.alpha = $alpha, r.beta = $beta, r.current_weight = toFloat($alpha) / ($alpha + $beta), r.last_updated = $now`
- `current_weight` is computed as `alpha / (alpha + beta)` (mean of Beta distribution)

### Pydantic model: `FiringEdge`

Defined in `services/prediction/graph/models.py`:

```python
from pydantic import BaseModel

class FiringEdge(BaseModel):
    source_event_type: str
    asset_symbol: str
    asset_name: str
    direction: str  # "UP" or "DOWN"
    current_weight: float
    base_weight: float
    alpha: int
    beta: int
    hop: int = 1  # 1 = direct, 2 = via intermediate asset
    via_asset: str | None = None  # populated for hop=2 edges
```

### File: `infra/neo4j/schema.cypher`

Contains:
```cypher
CREATE CONSTRAINT event_type_unique IF NOT EXISTS
  FOR (e:Event) REQUIRE e.type IS UNIQUE;

CREATE CONSTRAINT asset_symbol_unique IF NOT EXISTS
  FOR (a:Asset) REQUIRE a.symbol IS UNIQUE;

CREATE INDEX event_keywords IF NOT EXISTS
  FOR (e:Event) ON (e.keywords);

CREATE INDEX asset_class IF NOT EXISTS
  FOR (a:Asset) ON (a.asset_class);
```

### Environment variables (loaded via `python-dotenv` or Docker env)

| Variable | Example | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `changeme` | Neo4j password |

## Acceptance Criteria

1. `GraphMatcher` can be instantiated with valid Neo4j credentials without raising an exception.
2. `match_event_to_nodes('military_conflict', ['war', 'attack'])` returns a list containing `'military_conflict'` after seed data is loaded.
3. `get_firing_subgraph('military_conflict', ['war'])` returns at least one `FiringEdge` with `asset_symbol='GC=F'` and `direction='UP'` after seed data is loaded.
4. `get_firing_subgraph` returns multi-hop edges (hop=2) when an intermediate asset has outbound AFFECTS edges (e.g. `strait_closure -> OIL -> USD -> GOLD` chain).
5. `update_edge_weight('military_conflict', 'GC=F', 'UP', 5, 2)` updates `current_weight` to `5/7 ≈ 0.714` and sets `last_updated`.
6. `infra/neo4j/schema.cypher` applies without errors on a fresh Neo4j 5.x instance.
7. All tests in `services/prediction/tests/test_neo4j_client.py` pass (use a real Neo4j test container or mock the driver).
8. `ruff check services/prediction/graph/` and `mypy services/prediction/graph/` both exit 0.

## Implementation Notes

- Use `neo4j>=5.0` async driver. Session must be opened per query using `async with self._driver.session() as session:`.
- For multi-hop traversal, use a Cypher UNION or a two-step OPTIONAL MATCH rather than a variable-length path, to keep the result structure flat and easy to parse in Python.
- Sample multi-hop Cypher pattern:
  ```cypher
  MATCH (e:Event)-[r1:AFFECTS]->(a1:Asset)
  WHERE e.type = $event_type OR ANY(k IN e.keywords WHERE k IN $entities)
  OPTIONAL MATCH (a1)-[r2:AFFECTS]->(a2:Asset)
  RETURN e.type AS src, a1, r1, a2, r2
  ```
- `current_weight` formula used by Credibility Service is `alpha / (alpha + beta)`. Keep this consistent - do NOT use a different formula here.
- Neo4j Python driver record access: use `record['field']` syntax; fields from `RETURN` clause become dict keys.
- Do not hardcode connection details. Always read from environment variables.
- For unit tests, use `pytest-asyncio` and either:
  - A real Neo4j instance via `testcontainers-python` (`pip install testcontainers[neo4j]`), OR
  - Mock the `AsyncDriver` and `AsyncSession` using `unittest.mock.AsyncMock`
- The `FiringEdge.hop` field distinguishes direct (1) from indirect (2) edges. The graph-only decision policy uses this to weight or explain indirect edges more conservatively.

## Definition of Done

- [ ] Unit tests pass (`pytest services/prediction/tests/test_neo4j_client.py`)
- [ ] `ruff check services/prediction/` exits 0
- [ ] `mypy services/prediction/graph/` exits 0
- [ ] `infra/neo4j/schema.cypher` successfully applies on a fresh Neo4j 5.x instance
- [ ] `GraphMatcher.get_firing_subgraph` returns correct multi-hop edges with `hop=2` and `via_asset` populated
- [ ] `GraphMatcher.update_edge_weight` correctly computes `current_weight = alpha / (alpha + beta)`
- [ ] All environment variables documented in task are read from env, not hardcoded
- [ ] `services/prediction/graph/__init__.py` exports `GraphMatcher` and `FiringEdge`
