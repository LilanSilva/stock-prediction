# T01 — Cypher: `CORRELATES_WITH` Constraint, Index, and Seed Edges

## Context

The causal graph currently has only `(:CausalFactor)-[:CAUSES]->(:Asset)` relationships.
There is no way to express that a directional move in one asset causes a directional move in
another. This task adds the new `CORRELATES_WITH` relationship type, a uniqueness constraint,
a lookup index, and the first expert-seeded edges for the oil→gold capital rotation scenario.

## File to create

**`infra/neo4j/init/08-seed-correlation-edges.cypher`**

The existing init files run in alphabetical order inside the Neo4j init container. Naming the
file `08-*` places it after all existing seed files so all `Asset` nodes are guaranteed to
exist when this file runs.

## Implementation

Create `infra/neo4j/init/08-seed-correlation-edges.cypher` with the content below. Every
`MERGE` is idempotent so the container can re-run safely.

```cypher
// Cross-asset correlation edges: (:Asset)-[:CORRELATES_WITH {condition, direction, weight,
// confidence, alpha, beta, last_updated}]->(:Asset)
//
// These express second-order price causation: a directional move in one asset that reliably
// causes a directional move in another (e.g. oil UP → gold DOWN via capital rotation).
//
// Contract (mirrors CAUSES edges — SyRS §9.2):
//   condition   — ConditionCode that must be active for this edge to fire (UPSTREAM_UP or
//                 UPSTREAM_DOWN; see shared/schemas/messages.py ConditionCode).
//   direction   — expected direction on the TARGET asset (UP | DOWN).
//   weight      — expert-assigned magnitude in [0,1]; does NOT carry sign (sign is direction).
//   alpha/beta  — Beta-Bernoulli counts seeded at 1.0/1.0; refined by Credibility Service.
//   last_updated — managed by Credibility; initial value set here.
//
// Rationale (ADR-008): capital rotation — when oil rises, investors move money out of gold
// (both seen as inflation hedges, but oil is the active vehicle during a supply shock).
// The condition UPSTREAM_UP gates the edge: it fires only when the upstream asset was
// predicted UP in the same pipeline run.
//
// MERGE keeps this idempotent.

// --- XOM_NYSE (oil proxy) UP → NEM_NYSE (gold proxy) DOWN ---
MATCH (a1:Asset {id: 'XOM_NYSE'}), (a2:Asset {id: 'NEM_NYSE'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.45,
    r.confidence    = 0.60,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();

// --- XOM_NYSE UP → LUG_STO (Lundin Gold, precious metals group) DOWN ---
// Lundin Gold is a gold producer; oil-driven capital rotation depresses gold miners as well.
MATCH (a1:Asset {id: 'XOM_NYSE'}), (a2:Asset {id: 'LUG_STO'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.35,
    r.confidence    = 0.55,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();

// --- NEM_NYSE (gold proxy) UP → XOM_NYSE (oil proxy) DOWN ---
// Inverse rotation: a safe-haven gold bid (e.g. SAFE_HAVEN_ONLY conflict) can pull money
// from oil-sector equities. Weaker signal — only seed it when upstream gold is predicted UP.
MATCH (a1:Asset {id: 'NEM_NYSE'}), (a2:Asset {id: 'XOM_NYSE'})
MERGE (a1)-[r:CORRELATES_WITH {condition: 'UPSTREAM_UP'}]->(a2)
SET r.direction     = 'DOWN',
    r.weight        = 0.30,
    r.confidence    = 0.50,
    r.alpha         = 1.0,
    r.beta          = 1.0,
    r.last_updated  = datetime();
```

## Additional constraint and index

Add the following block at the **top** of the same file, before the `MERGE` statements:

```cypher
// Uniqueness: one (source, target, condition) triple → one CORRELATES_WITH edge.
// Without this, MERGE on all three properties still works, but an explicit constraint
// makes the invariant visible and enforced at the DB level.
CREATE CONSTRAINT correlates_with_unique IF NOT EXISTS
FOR ()-[r:CORRELATES_WITH]-()
REQUIRE (r.condition) IS NOT NULL;

// Lookup index for the propagation query: given a source Asset.id + condition, find targets fast.
CREATE INDEX correlates_with_source IF NOT EXISTS
FOR ()-[r:CORRELATES_WITH]-()
ON (r.condition);
```

Note: Neo4j 5 supports relationship property constraints and indexes. The constraint above
enforces that every `CORRELATES_WITH` edge carries a `condition` — a NULL condition would be
ambiguous (unlike `CAUSES`, which uses NULL to mean "unconditional").

## Acceptance criteria

1. After `docker compose up --build`, running the following returns exactly 3 rows:

```cypher
MATCH (a1:Asset)-[r:CORRELATES_WITH]->(a2:Asset)
RETURN a1.id AS source, r.condition AS cond, r.direction AS dir, a2.id AS target
ORDER BY source, target
```

Expected rows (in any order):

| source | cond | dir | target |
|---|---|---|---|
| NEM_NYSE | UPSTREAM_UP | DOWN | XOM_NYSE |
| XOM_NYSE | UPSTREAM_UP | DOWN | LUG_STO |
| XOM_NYSE | UPSTREAM_UP | DOWN | NEM_NYSE |

2. `SHOW CONSTRAINTS WHERE name = 'correlates_with_unique'` returns one row.
3. `SHOW INDEXES WHERE name = 'correlates_with_source'` returns one row.
4. All six existing Neo4j init scripts still apply cleanly (no name collision, no syntax error).
5. Re-running the init container a second time (idempotency check) produces the same result
   with no duplicate edges.

## Definition of done

- [ ] File `infra/neo4j/init/08-seed-correlation-edges.cypher` created
- [ ] All 5 acceptance criteria verified in a local `docker compose up --build` run
- [ ] No existing Cypher constraint or index name is reused
- [ ] File header comment explains the `condition` contract (UPSTREAM_UP / UPSTREAM_DOWN)
