# S01 — Graph Schema: `CORRELATES_WITH` Edge

## Overview

Add the new `(:Asset)-[:CORRELATES_WITH {condition, direction, weight, confidence, alpha, beta,
last_updated}]->(:Asset)` relationship type to Neo4j, seed the first domain examples (oil→gold
capital rotation, oil→gold-miners inheritance), and add a constraint and index so the new edge
type is queryable efficiently.

This story has no Python code changes — it is infrastructure-only. S02 and S03 depend on it
being deployed before they can be tested end-to-end.

## Dependencies

- None outside this epic. All existing services continue to work unchanged after this story is
  applied because the new edge type is additive only.

## Tasks

| Task | Summary |
|---|---|
| [T01](T01-correlates-with-edge-seed.md) | Cypher: constraint, index, and seed edges |

## How to test end-to-end

1. `docker compose down -v && docker compose up --build` to let Neo4j init scripts re-run.
2. Open the Neo4j Browser (`http://localhost:7474`) and run:

```cypher
MATCH (a1:Asset)-[r:CORRELATES_WITH]->(a2:Asset)
RETURN a1.id, r, a2.id
```

Expected: at least two rows (XOM_NYSE → NEM_NYSE and XOM_NYSE → LUG_STO group-inherited row).

3. Confirm the constraint exists:

```cypher
SHOW CONSTRAINTS WHERE name STARTS WITH 'correlates'
```
