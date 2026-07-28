# T02: Seed Initial Commodity Knowledge Graph

> **⚠️ SUPERSEDED — already delivered by E01 with the canonical schema.** The `Event`/`AFFECTS`
> seed with provider symbols (`GC=F`, `DX-Y.NYB`, `^OMXS30`, `^GSPC`), keyword lists, `base_weight`,
> and 33 edges below is **not** what ships. The authoritative seed lives in `infra/neo4j/init/`
> (`02-seed-assets.cypher`, `03-seed-causal-factors.cypher`, `04-seed-causal-edges.cypher`) and
> creates `(:CausalFactor {id=EventType})-[:CAUSES {direction, weight, confidence, alpha=1.0,
> beta=1.0}]->(:Asset {id})` with **15 canonical edges over GOLD and BRENT_OIL only** (USD/OMXS30/
> SP500 are deferred assets; provider symbols belong only in Market Data adapters). MERGE keeps it
> idempotent. `weight` is magnitude in [0,1]; direction is a separate field.

## Context

The Prediction Service needs a pre-populated Neo4j knowledge graph to query at startup. This task creates the seed script that loads initial expert-defined causal edges into Neo4j. Without seed data, `get_firing_subgraph` always returns empty results and the Prediction Service produces no predictions. This seed represents domain knowledge that will be refined over time by the Credibility Service's Bayesian updates.

## Background

The seed script creates `Event` nodes, `Asset` nodes, and `AFFECTS` directed relationships between them. Each `AFFECTS` relationship encodes the direction of the causal effect (`UP` or `DOWN`) and an initial expert-assigned weight (`base_weight`, mirrored to `current_weight`). The Beta-Bernoulli counters (`alpha=1`, `beta=1`) are initialized to a uniform prior - equivalent to "no data yet, assume 50/50".

**Assets to seed** (Neo4j `Asset` nodes with their symbols):

| Symbol | Name | Asset Class |
|---|---|---|
| `GC=F` | Gold Futures | COMMODITY |
| `BZ=F` | Brent Crude Oil | COMMODITY |
| `DX-Y.NYB` | US Dollar Index | CURRENCY |
| `^OMXS30` | OMX Stockholm 30 | EQUITY_INDEX |
| `^GSPC` | S&P 500 | EQUITY_INDEX |

**Event types to seed** (Neo4j `Event` nodes with their keywords):

| Type | Keywords |
|---|---|
| `military_conflict` | war, attack, invasion, strike, conflict, bombing, troops, missile |
| `supply_disruption` | supply, shortage, disruption, production cut, strike, output reduction |
| `strait_closure` | strait, blockade, tanker, shipping lane, chokepoint, Hormuz, Malacca |
| `rate_hike` | interest rate, hike, Fed, Riksbank, ECB, tightening, basis points |
| `rate_cut` | rate cut, easing, dovish, stimulus, QE, quantitative easing |
| `recession_fear` | recession, slowdown, contraction, GDP decline, unemployment, layoffs |
| `inflation_surge` | inflation, CPI, PPI, price surge, hyperinflation, cost of living |
| `geopolitical_tension` | sanctions, tariff, trade war, embargo, diplomatic crisis, expulsion |
| `equity_selloff` | crash, selloff, correction, bear market, panic, circuit breaker |
| `central_bank_intervention` | intervention, currency peg, FX reserve, capital controls |

**Required causal edges (minimum 30)**:

| From Event | To Asset | Direction | Base Weight | Rationale |
|---|---|---|---|---|
| military_conflict | GC=F | UP | 0.75 | Safe haven demand |
| military_conflict | BZ=F | UP | 0.65 | Supply disruption risk |
| military_conflict | DX-Y.NYB | UP | 0.45 | Safe haven demand for USD |
| military_conflict | ^GSPC | DOWN | 0.55 | Risk-off equity sell |
| military_conflict | ^OMXS30 | DOWN | 0.50 | Risk-off equity sell |
| supply_disruption | BZ=F | UP | 0.90 | Direct supply shock |
| supply_disruption | GC=F | UP | 0.35 | Inflation hedge |
| supply_disruption | ^GSPC | DOWN | 0.45 | Input cost pressure |
| strait_closure | BZ=F | UP | 0.90 | Shipping route disruption |
| strait_closure | DX-Y.NYB | UP | 0.40 | Oil priced in USD, demand rises |
| strait_closure | GC=F | DOWN | 0.30 | Oil dominates, gold less relevant |
| rate_hike | DX-Y.NYB | UP | 0.70 | Higher yield attracts capital |
| rate_hike | GC=F | DOWN | 0.65 | Opportunity cost of holding gold |
| rate_hike | ^GSPC | DOWN | 0.60 | Discounted future earnings lower |
| rate_hike | ^OMXS30 | DOWN | 0.55 | Same equity discount effect |
| rate_cut | DX-Y.NYB | DOWN | 0.65 | Lower yield reduces capital inflows |
| rate_cut | GC=F | UP | 0.60 | Lower opportunity cost of gold |
| rate_cut | ^GSPC | UP | 0.55 | Higher valuations from lower discount rate |
| rate_cut | ^OMXS30 | UP | 0.50 | Same equity re-rating effect |
| recession_fear | GC=F | UP | 0.55 | Flight to safety |
| recession_fear | ^GSPC | DOWN | 0.70 | Earnings contraction expected |
| recession_fear | ^OMXS30 | DOWN | 0.65 | Export-dependent Swedish market |
| recession_fear | BZ=F | DOWN | 0.60 | Demand destruction for oil |
| inflation_surge | GC=F | UP | 0.65 | Inflation hedge demand |
| inflation_surge | BZ=F | UP | 0.50 | Energy costs feeding inflation |
| inflation_surge | DX-Y.NYB | DOWN | 0.40 | Real yield erosion |
| geopolitical_tension | GC=F | UP | 0.60 | Risk premium, safe haven |
| geopolitical_tension | ^GSPC | DOWN | 0.45 | Uncertainty discount |
| geopolitical_tension | ^OMXS30 | DOWN | 0.50 | Swedish market sensitivity to EU tensions |
| equity_selloff | GC=F | UP | 0.70 | Flight to safety from equities |
| equity_selloff | DX-Y.NYB | UP | 0.35 | USD safe haven |
| central_bank_intervention | DX-Y.NYB | DOWN | 0.55 | Selling USD to defend currency |
| central_bank_intervention | GC=F | UP | 0.40 | Diversification out of USD |

Total: 33 edges.

## Inputs

- Neo4j instance running with schema applied (T01's `infra/neo4j/schema.cypher` executed first)
- Environment variables: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`

## Outputs

- Seed script: `infra/neo4j/seed_knowledge_graph.py` (Python, runnable standalone)
- Companion Cypher file: `infra/neo4j/seed_knowledge_graph.cypher` (human-readable, same edges)
- After execution: 10 `Event` nodes, 5 `Asset` nodes, and 33 `AFFECTS` relationships in Neo4j

## Technical Requirements

### File: `infra/neo4j/seed_knowledge_graph.py`

- Uses `neo4j` sync driver (simpler for a one-off seed script): `from neo4j import GraphDatabase`
- Reads connection details from environment variables (use `os.environ` or `python-dotenv`)
- Uses `MERGE` not `CREATE` so it is **idempotent** - re-running does not duplicate nodes or edges
- Prints a summary line after completion: `Seeded {n_events} events, {n_assets} assets, {n_edges} edges.`
- Exits with code 0 on success, 1 on connection error

**Cypher pattern for each edge (use `MERGE` for idempotency)**:
```cypher
MERGE (e:Event {type: $event_type})
  ON CREATE SET e.keywords = $keywords
MERGE (a:Asset {symbol: $asset_symbol})
  ON CREATE SET a.name = $asset_name, a.asset_class = $asset_class
MERGE (e)-[r:AFFECTS {direction: $direction}]->(a)
  ON CREATE SET
    r.base_weight    = $base_weight,
    r.current_weight = $base_weight,
    r.alpha          = 1,
    r.beta           = 1,
    r.last_updated   = $now
```

- `$now` = current UTC ISO-8601 string: `datetime.utcnow().isoformat()`
- The `MERGE` on the relationship must include `direction` in its match clause so `(military_conflict)-[:AFFECTS {direction:'UP'}]->(GC=F)` and a hypothetical `(military_conflict)-[:AFFECTS {direction:'DOWN'}]->(GC=F)` are stored as separate relationships.

### File: `infra/neo4j/seed_knowledge_graph.cypher`

Contains the same operations as the Python script but in raw Cypher. Used for manual inspection and for running directly in Neo4j Browser. Parameters should be inlined as literals (not `$params`).

### Data structure in seed script

Define seed data as a Python list of dicts:
```python
EDGES = [
    {
        "event_type": "military_conflict",
        "keywords": ["war", "attack", "invasion", ...],
        "asset_symbol": "GC=F",
        "asset_name": "Gold Futures",
        "asset_class": "COMMODITY",
        "direction": "UP",
        "base_weight": 0.75,
    },
    # ... all 33 edges
]
```

## Acceptance Criteria

1. Running `python infra/neo4j/seed_knowledge_graph.py` against a fresh Neo4j 5.x instance completes without error.
2. After running, `MATCH (e:Event) RETURN count(e)` returns 10.
3. After running, `MATCH (a:Asset) RETURN count(a)` returns 5.
4. After running, `MATCH ()-[r:AFFECTS]->() RETURN count(r)` returns 33.
5. Every `AFFECTS` edge has properties: `direction` (UP or DOWN), `base_weight` (float), `current_weight` (float equal to `base_weight`), `alpha` (int = 1), `beta` (int = 1), `last_updated` (non-null string).
6. Running the seed script a second time (idempotency check) does not change the edge count - still 33.
7. `MATCH (e:Event {type:'military_conflict'})-[r:AFFECTS]->(a:Asset {symbol:'GC=F'}) RETURN r.direction, r.base_weight` returns `UP, 0.75`.
8. `MATCH (e:Event {type:'rate_hike'})-[r:AFFECTS]->(a) RETURN a.symbol, r.direction` returns at least `DX-Y.NYB:UP`, `GC=F:DOWN`, `^GSPC:DOWN`.
9. `infra/neo4j/seed_knowledge_graph.cypher` can be pasted into Neo4j Browser and executed without errors.
10. `ruff check infra/neo4j/seed_knowledge_graph.py` exits 0.

## Implementation Notes

- Use the **synchronous** `neo4j.GraphDatabase` driver in the seed script (not async). This is a CLI tool, not a service, and sync is simpler.
- Use a single transaction for all MERGE statements for atomicity: wrap the loop inside `with session.begin_transaction() as tx:` and call `tx.run(cypher, params)` per edge, then `tx.commit()`.
- If the transaction is too large (>1000 operations), split into batches of 100. With 33 edges this is not a concern.
- `MERGE (e)-[r:AFFECTS {direction: $direction}]->(a)` - including `direction` in the MERGE pattern is critical. Neo4j allows multiple relationships of the same type between the same nodes if their properties differ. Without `direction` in the MERGE key, running the script twice would try to create duplicate edges and fail or merge incorrectly.
- The `base_weight` field is intentionally never updated after seeding. It serves as the "original expert opinion" anchor. The Credibility Service only modifies `current_weight`, `alpha`, and `beta`.
- Do not add edges that would create logical contradictions (e.g. `military_conflict -> GC=F UP` AND `military_conflict -> GC=F DOWN`). M1 graph-only prediction handles ambiguity between different event types affecting the same asset; a single event type should not point both ways to the same asset.
- The seed script should be runnable from the repo root: `python infra/neo4j/seed_knowledge_graph.py`. Use relative imports or ensure `infra/neo4j/` is self-contained.

## Definition of Done

> Verified against the delivered canonical seed in `infra/neo4j/init/`; the legacy Python seed,
> provider-symbol assets, keyword lists, and 33-edge count are superseded.

- [x] The canonical Cypher seed applies cleanly against a running Neo4j (via the one-shot `feed-neo4j-seed` container)
- [x] At least 15 `CAUSES` edges exist after seeding (canonical scope; the legacy “exactly 33” is superseded)
- [x] Edge count is stable after re-running (idempotent via MERGE)
- [x] The canonical POC assets are present: `GOLD`, `BRENT_OIL` (USD/OMXS30/SP500 deferred; no provider symbols on nodes)
- [x] All 10 non-OTHER `EventType` values are present as `CausalFactor` nodes
- [x] The seed is Cypher under `infra/neo4j/init/` (no separate Python seed script)
- [x] Seed validated by `src/shared/tests/test_infrastructure.py::test_neo4j_seed_has_canonical_assets_and_minimum_edges`
- [x] Seed is idempotent (MERGE-based, not CREATE-based)
