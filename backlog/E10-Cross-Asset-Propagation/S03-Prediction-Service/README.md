# S03 — Prediction Service: Two-Pass Propagation Loop

## Overview

Extend the Prediction Service pipeline to run a second pass after direct predictions are
produced. The second pass queries `CORRELATES_WITH` edges for each asset that received a
directional prediction and runs `decide()` for their downstream targets. The process repeats
until no new predictions are produced or the depth cap is reached.

`decide()` in `decision.py` is **not modified**. All new logic lives in `pipeline.py`.

## Dependencies

- S01 (correlation edges in Neo4j) must be deployed.
- S02 T01, T02, T03 must be merged (new `ConditionCode` values, `PropagationHop`,
  `CorrelationEdge`, updated `PredictionMade`, new graph-client methods).

## Tasks

| Task | Summary |
|---|---|
| [T01](T01-propagation-loop.md) | Two-pass loop, visited set, depth cap in `pipeline.py` |
| [T02](T02-config-depth-cap.md) | `MAX_PROPAGATION_DEPTH` config key in `config.py` |

## How to test end-to-end

1. Ensure the Neo4j `CORRELATES_WITH` seed edges from S01 are present.
2. Publish an `EventDetected` message with `event_type=MILITARY_CONFLICT`,
   `context_tags=[TRANSPORT_AFFECTED]`, and `asset_ids=[XOM_NYSE]`.
3. Observe that two `PredictionMade` messages are published:
   - `asset_id=XOM_NYSE`, `propagation_depth=0`
   - `asset_id=NEM_NYSE`, `propagation_depth=1`, `propagation_chain` contains one hop
     `XOM_NYSE|UPSTREAM_UP->NEM_NYSE`.
4. Publish a second `EventDetected` for the same context window and confirm idempotency:
   no duplicate predictions, visited-set guard fires.
