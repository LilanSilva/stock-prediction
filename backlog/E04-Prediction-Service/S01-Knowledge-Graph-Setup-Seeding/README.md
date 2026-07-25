# S01 - Knowledge Graph Setup & Seeding

## Overview

This story establishes the Neo4j knowledge graph that is the causal backbone of the entire prediction pipeline. It defines the graph schema (node types, relationship properties, indexes), the Cypher query interface used by the Prediction Service at runtime, and the initial seed data of ~30 signed/weighted causal edges covering the major assets tracked by the system.

Without this story, the Prediction Engine (S02) cannot function - it has no graph to query.

## Tasks

| Task | Name | Description |
|---|---|---|
| T01 | Knowledge graph schema & Cypher queries | Node types, relationship properties, indexes, and the three Cypher query functions used at runtime |
| T02 | Seed initial commodity knowledge graph | Cypher seed script with ~30 initial AFFECTS edges covering GOLD, OIL, USD, OMXS30, SPX |

## Dependencies

- Neo4j instance running (via `infra/docker-compose.yml`)
- `neo4j` Python driver available (`neo4j>=5.0` pip package)
- `src/shared/` package structure exists (T01 outputs a module under `services/prediction/graph/`)

## End-to-End Test

1. Start Neo4j via `docker compose up neo4j`.
2. Run the seed script: `python infra/neo4j/seed_knowledge_graph.py`.
3. Open Neo4j Browser at `http://localhost:7474` and run: `MATCH (e:Event)-[r:AFFECTS]->(a:Asset) RETURN e, r, a LIMIT 50`
4. Verify at least 30 edges are visible with `direction`, `base_weight`, `current_weight`, `alpha`, `beta` properties.
5. From a Python REPL, instantiate `GraphMatcher` and call `get_firing_subgraph('military_conflict', ['GOLD'])` - expect at least one result.
