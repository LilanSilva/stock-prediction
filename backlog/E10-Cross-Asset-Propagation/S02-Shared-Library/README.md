# S02 — Shared Library Extensions

## Overview

Extend `src/shared/` with everything the Prediction Service (S03) and Credibility Service
(S04) need to work with `CORRELATES_WITH` edges:

- Two new `ConditionCode` values (`UPSTREAM_UP`, `UPSTREAM_DOWN`)
- A `PropagationHop` value model
- Two new fields on `PredictionMade` (`propagation_depth`, `propagation_chain`)
- Two new `CausalGraphClient` methods (`get_correlation_edges`, `update_correlation_weight`)

No service business logic changes in this story — only the shared contract layer.

## Dependencies

- S01 must be complete so the new `CORRELATES_WITH` edges exist in Neo4j for integration
  testing.

## Tasks

| Task | Summary |
|---|---|
| [T01](T01-condition-codes-and-models.md) | Add `ConditionCode` values and `PropagationHop` model |
| [T02](T02-prediction-made-schema.md) | Extend `PredictionMade` with propagation fields |
| [T03](T03-graph-client-correlation-methods.md) | Add `get_correlation_edges` and `update_correlation_weight` to `CausalGraphClient` |

## How to test end-to-end

```bash
cd src/shared
pytest tests/ -v
mypy shared --strict
ruff check shared
```

All existing tests must pass. New tests from T01–T03 must also pass.
