# S01 - Knowledge Graph Setup & Seeding

## Overview

This story establishes the Neo4j knowledge graph that is the causal backbone of the entire prediction pipeline. It defines the graph schema (node types, relationship properties, indexes), the Cypher query interface used by the Prediction Service at runtime, and the initial seed data of ~30 signed/weighted causal edges covering the major assets tracked by the system.

Without this story, the Prediction Engine (S02) cannot function - it has no graph to query.

## Tasks

| Task | Name | Description |
|---|---|---|
| T01 | Knowledge graph schema & Cypher queries | Node types, relationship properties, indexes, and the three Cypher query functions used at runtime |
| T02 | Seed initial commodity knowledge graph | Canonical `CAUSES` seed (≥15 edges) over GOLD and BRENT_OIL — **already delivered by E01** in `infra/neo4j/init/`. The legacy `AFFECTS`/USD/OMXS30/SPX seed is superseded. |

## Dependencies

- Neo4j instance running (via `infra/docker-compose.yml`)
- `neo4j` Python driver available (`neo4j>=5.0` pip package)
- `src/shared/` package structure exists (T01 outputs a module under `src/services/prediction/graph/`)

## End-to-End Test

1. Start Neo4j via `docker compose up neo4j`.
2. Run the seed script: `python infra/neo4j/seed_knowledge_graph.py`.
3. Open Neo4j Browser at `http://localhost:7474` and run: `MATCH (cf:CausalFactor)-[r:CAUSES]->(a:Asset) RETURN cf, r, a LIMIT 50`
4. Verify at least 30 edges are visible with `direction`, `base_weight`, `current_weight`, `alpha`, `beta` properties.
5. From a Python REPL, instantiate `GraphMatcher` and call `get_firing_subgraph('military_conflict', ['GOLD'])` - expect at least one result.
