# T03: Neo4j Initialization & Schema

## Context

This task creates the Cypher initialization scripts that set up the Neo4j knowledge graph schema and seed it with an initial set of causal edges between macro-economic assets and event types. The Prediction Service reads this graph at prediction time to find causal chains that link news events to asset price movements. The Credibility Service updates edge weights based on prediction outcomes.

This task belongs to the infrastructure story (S01). The files are volume-mounted into the `feed-neo4j-seed` init container defined in T01.

## Background

### What the knowledge graph represents

The graph models causal relationships of the form: **"event X tends to move asset Y in direction Z with weight W"**. For example:
- `(war_escalation)-[:CAUSES {direction: 'UP', weight: 0.7, confidence: 0.8}]->(gold_price)`
- `(fed_rate_hike)-[:CAUSES {direction: 'DOWN', weight: -0.6, confidence: 0.75}]->(equity_indices)`

The Prediction Service does multi-hop traversal: a news event triggers one or more causal nodes, which then fan out to affected assets. M1 uses graph-only policy to resolve the resulting signed forces; prediction-time LLM arbitration is deferred after POC-6.

### Graph schema

Two node types:
- `Asset` — a tradeable instrument: `{id: string, name: string, ticker: string, asset_class: string}`
- `CausalFactor` — a macro-economic or geopolitical event type: `{id: string, name: string, category: string}`

One relationship type:
- `CAUSES` — from `CausalFactor` to `Asset`: `{direction: 'UP'|'DOWN'|'NEUTRAL', weight: float (-1.0 to 1.0), confidence: float (0.0 to 1.0), alpha: int, beta: int, last_updated: datetime}`

The `alpha` and `beta` fields are the Beta-Bernoulli parameters used by the Credibility Service for Bayesian weight updates. Initial seed values: `alpha=2, beta=2` (weakly informative prior, equivalent to having seen 2 hits and 2 misses).

## Inputs

- T01's Docker Compose definition of the `feed-neo4j-seed` init container, which mounts `./neo4j/init/` to `/seed/` and runs `cypher-shell` on each `.cypher` file.
- Neo4j bolt endpoint: `bolt://feed-neo4j:7687`, credentials `neo4j/feedpassword` (from `.env`).

## Outputs

Files created in `infra/neo4j/init/`:

```
infra/neo4j/init/
  01-constraints-indexes.cypher
  02-seed-assets.cypher
  03-seed-causal-factors.cypher
  04-seed-causal-edges.cypher
```

## Technical Requirements

### `01-constraints-indexes.cypher`

Create uniqueness constraints (which also create indexes) on node IDs:

```cypher
CREATE CONSTRAINT asset_id_unique IF NOT EXISTS
  FOR (a:Asset) REQUIRE a.id IS UNIQUE;

CREATE CONSTRAINT causal_factor_id_unique IF NOT EXISTS
  FOR (c:CausalFactor) REQUIRE c.id IS UNIQUE;

CREATE INDEX asset_ticker_index IF NOT EXISTS
  FOR (a:Asset) ON (a.ticker);

CREATE INDEX causal_factor_category_index IF NOT EXISTS
  FOR (c:CausalFactor) ON (c.category);
```

### `02-seed-assets.cypher`

Seed at minimum the following assets (use `MERGE` for idempotency):

| id | name | ticker | asset_class |
|---|---|---|---|
| `gold` | Gold Spot | `GC=F` | `commodity` |
| `oil_wti` | WTI Crude Oil | `CL=F` | `commodity` |
| `oil_brent` | Brent Crude Oil | `BZ=F` | `commodity` |
| `usd_index` | US Dollar Index | `DX-Y.NYB` | `currency` |
| `sp500` | S&P 500 | `^GSPC` | `equity_index` |
| `nasdaq` | NASDAQ 100 | `^NDX` | `equity_index` |
| `omxs30` | OMX Stockholm 30 | `^OMXS30` | `equity_index` |
| `usd_sek` | USD/SEK | `USDSEK=X` | `forex` |
| `eur_sek` | EUR/SEK | `EURSEK=X` | `forex` |
| `swedish_10y` | Swedish 10Y Bond | `SE10Y` | `bond` |

Example Cypher:
```cypher
MERGE (a:Asset {id: 'gold'})
SET a.name = 'Gold Spot',
    a.ticker = 'GC=F',
    a.asset_class = 'commodity';
```

### `03-seed-causal-factors.cypher`

Seed at minimum the following causal factors:

| id | name | category |
|---|---|---|
| `geopolitical_conflict` | Geopolitical Conflict / War | `geopolitical` |
| `fed_rate_decision` | US Federal Reserve Rate Decision | `monetary_policy` |
| `riksbank_rate_decision` | Riksbank Rate Decision | `monetary_policy` |
| `us_inflation_data` | US CPI / Inflation Data | `economic_data` |
| `swedish_inflation_data` | Swedish CPI / Inflation Data | `economic_data` |
| `oil_supply_shock` | Oil Supply Disruption | `commodity_supply` |
| `us_recession_fear` | US Recession Signals | `macro_sentiment` |
| `dollar_strength` | USD Strengthening | `currency` |
| `sanctions` | International Sanctions | `geopolitical` |
| `corporate_earnings_beat` | Strong Corporate Earnings | `corporate` |
| `banking_crisis` | Banking Sector Stress | `financial_stability` |
| `energy_crisis` | European Energy Crisis | `commodity_supply` |

### `04-seed-causal-edges.cypher`

Seed at minimum 15 causal edges. Use `MERGE` on the relationship to be idempotent. Set initial Beta-Bernoulli parameters `alpha=2, beta=2`.

Required edges (seed these exactly, additional edges are welcome):

| From | To | direction | weight | confidence |
|---|---|---|---|---|
| `geopolitical_conflict` | `gold` | UP | 0.75 | 0.80 |
| `geopolitical_conflict` | `oil_wti` | UP | 0.65 | 0.75 |
| `geopolitical_conflict` | `usd_index` | UP | 0.50 | 0.65 |
| `geopolitical_conflict` | `sp500` | DOWN | -0.55 | 0.70 |
| `geopolitical_conflict` | `omxs30` | DOWN | -0.50 | 0.65 |
| `fed_rate_hike` | `gold` | DOWN | -0.60 | 0.75 |
| `fed_rate_hike` | `sp500` | DOWN | -0.65 | 0.80 |
| `fed_rate_hike` | `usd_index` | UP | 0.70 | 0.80 |
| `fed_rate_hike` | `usd_sek` | UP | 0.55 | 0.65 |
| `oil_supply_shock` | `oil_wti` | UP | 0.85 | 0.90 |
| `oil_supply_shock` | `oil_brent` | UP | 0.85 | 0.90 |
| `oil_supply_shock` | `omxs30` | DOWN | -0.40 | 0.60 |
| `dollar_strength` | `gold` | DOWN | -0.65 | 0.75 |
| `dollar_strength` | `oil_wti` | DOWN | -0.50 | 0.65 |
| `banking_crisis` | `gold` | UP | 0.70 | 0.75 |
| `banking_crisis` | `sp500` | DOWN | -0.75 | 0.80 |
| `energy_crisis` | `omxs30` | DOWN | -0.60 | 0.70 |
| `energy_crisis` | `eur_sek` | UP | 0.45 | 0.60 |

Example Cypher for one edge:
```cypher
MATCH (cf:CausalFactor {id: 'geopolitical_conflict'})
MATCH (a:Asset {id: 'gold'})
MERGE (cf)-[r:CAUSES]->(a)
SET r.direction = 'UP',
    r.weight = 0.75,
    r.confidence = 0.80,
    r.alpha = 2,
    r.beta = 2,
    r.last_updated = datetime();
```

## Acceptance Criteria

1. After `docker compose up`, running `MATCH (n:Asset) RETURN count(n)` in Neo4j Browser returns at least 10.
2. `MATCH (n:CausalFactor) RETURN count(n)` returns at least 12.
3. `MATCH ()-[r:CAUSES]->() RETURN count(r)` returns at least 15.
4. `SHOW CONSTRAINTS` lists both uniqueness constraints (`asset_id_unique`, `causal_factor_id_unique`).
5. `SHOW INDEXES` lists both lookup indexes (`asset_ticker_index`, `causal_factor_category_index`).
6. Every `CAUSES` relationship has `alpha`, `beta`, `weight`, `direction`, `confidence`, and `last_updated` properties.
7. `MATCH (cf:CausalFactor {id: 'geopolitical_conflict'})-[r:CAUSES]->(a:Asset {id: 'gold'}) RETURN r.weight` returns `0.75`.
8. The seed container (`feed-neo4j-seed`) exits with code 0 (check: `docker inspect feed-neo4j-seed --format='{{.State.ExitCode}}'`).
9. Running the seed scripts a second time (re-creating the seed container) does not create duplicate nodes or edges (idempotency via `MERGE`).

## Implementation Notes

- `cypher-shell` is available inside the `neo4j:5.20-community` image. No separate client installation needed.
- The seed container defined in T01 runs after `feed-neo4j` is `healthy`. The health check polls the HTTP endpoint. On first start, Neo4j can take 30-60 seconds to become healthy — the seed container's `depends_on` with `condition: service_healthy` handles this.
- Use `MERGE` rather than `CREATE` everywhere in seed files so the scripts are safe to re-run.
- `MERGE` on relationships requires matching both endpoints first with `MATCH`. Do not use `MERGE` for the endpoint nodes in the same statement as the relationship MERGE, or you risk creating duplicate nodes if the constraint has not been applied yet. Always separate the `MATCH` and `MERGE` statements.
- The `weight` field is a float between -1.0 (strong causal decrease) and +1.0 (strong causal increase). Magnitude encodes strength; sign encodes direction. The separate `direction` string field makes graph-only filtering and explanation easier.
- Neo4j datetime literals use `datetime()` (current UTC time). Always set `last_updated` on creation so the Credibility Service can track when an edge was last modified.
- If `cypher-shell` returns a non-zero exit code, the seed container will fail and Docker Compose will log it. Check `docker logs feed-neo4j-seed` to diagnose.

## Definition of Done

- [x] `infra/neo4j/init/01-constraints-indexes.cypher` exists and applies constraints and indexes (verified: 2 constraints)
- [ ] `infra/neo4j/init/02-seed-assets.cypher` exists and seeds at least 10 Asset nodes (POC scope seeds 2: `GOLD`, `BRENT_OIL` — legacy target superseded)
- [ ] `infra/neo4j/init/03-seed-causal-factors.cypher` exists and seeds at least 12 CausalFactor nodes (POC scope seeds 10 — legacy target superseded)
- [x] `infra/neo4j/init/04-seed-causal-edges.cypher` exists and seeds at least 15 CAUSES relationships (verified: 15)
- [x] All `.cypher` files use `MERGE` (not `CREATE`) for data idempotency (constraints/indexes correctly use `CREATE ... IF NOT EXISTS`)
- [x] `feed-neo4j-seed` container exits with code 0
- [ ] Queries in Acceptance Criteria 1-7 return expected results in Neo4j Browser
- [ ] `docker compose down -v && docker compose up` re-seeds correctly
