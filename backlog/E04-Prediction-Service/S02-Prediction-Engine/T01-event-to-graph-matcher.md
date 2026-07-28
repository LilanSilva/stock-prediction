# T01: Event-to-Graph Matcher

> **Delivered:** implemented as `prediction/pipeline.py` (context aggregation + close sweep),
> `prediction/context.py` (event-time windows), and `prediction/decision.py` (graph-only policy),
> reading the canonical graph via the shared `shared.graph.CausalGraphClient.get_firing_edges`.
> Single-hop for M1 (the seed has no Asset→Asset edges). No `shared.llm` import.

## Current Decision

M1 Prediction Service uses graph-only prediction. This task must not wire an LLM arbiter or call the shared LLM gateway.

## Purpose

Consume canonical `EventDetected` messages, add distinct events to the correct per-asset context window, query Neo4j for relevant causal graph edges, and pass grouped graph-force evidence to the graph-only decision policy.

## Inputs

- Queue: `prediction.events`, bound to `event.detected` on `feed.events`.
- Message: canonical `EventDetected`.
- Neo4j causal graph.
- Canonical asset registry and event taxonomy.

## Outputs

- Ready asset/context graph-force bundle for graph-only decision.
- No prediction is produced when no material graph edge fires.
- No LLM request is made.

## Requirements

- Use canonical `asset_id`, not provider symbols.
- Aggregate events into the configured 60-minute event-time context window.
- Preserve distinct event IDs; do not collapse multiple causal events into one event.
- Create a new context version for eligible late events.
- Query bounded causal paths relevant to the affected asset.
- Group signed graph forces by canonical `asset_id`.
- Include edge ID, direction, current weight, influence weight, hop/path provenance, and source event ID.
- Unknown asset IDs or event types are quarantined, not silently accepted.
- Duplicate event delivery must not duplicate context membership.

## Environment Variables

| Variable | Purpose |
|---|---|
| `RABBITMQ_URL` | RabbitMQ connection |
| `NEO4J_URI` | Neo4j Bolt URI |
| `NEO4J_USER` | Neo4j username |
| `NEO4J_PASSWORD` | Neo4j password |
| `POSTGRES_DSN` | Prediction schema connection |

No `LLM_*` configuration is required by Prediction in M1.

## Acceptance Criteria

1. The consumer reads canonical `EventDetected` messages from `prediction.events`.
2. Duplicate event delivery does not create duplicate context membership.
3. `_match_graph` returns force bundles keyed by canonical `asset_id`.
4. Empty graph results are acknowledged and recorded without publishing a prediction.
5. Single-hop firing edges retain factor/asset path provenance and are assigned to the affected asset (multi-hop is deferred — the canonical seed has no Asset→Asset edges).
6. The output is passed to graph-only decision policy, not an LLM arbiter.
7. No code imports provider SDKs or `shared.llm`.
8. Unit tests cover empty graph, single-asset, multi-asset, duplicate event, and exception paths.
9. `ruff check src/services/prediction/` and `mypy src/services/prediction/` pass.
