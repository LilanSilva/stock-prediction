# T02 — Update SyRS (System Specification)

## Context

`requirements/SyRS-system.md` is the system-level authority. It describes the end-to-end
flow, the graph model, the canonical messages, and the definitions shared across all services.
E10 adds a new graph edge type, two new fields on `PredictionMade`, and a new step in the
end-to-end flow. All of these must be reflected here.

Read the full file before editing. Make surgical edits only — do not rewrite sections that
are unchanged.

## Changes required

### 1. Section 3 — Definitions: add new terms

Find the definitions table and add:

```markdown
| `CORRELATES_WITH` edge | A directed, weighted link from one Asset to another, gated by a `ConditionCode` (`UPSTREAM_UP` or `UPSTREAM_DOWN`); fires during the propagation pass when the source asset was predicted directional |
| Propagation pass | A second `decide()` sweep in Prediction over assets reachable via `CORRELATES_WITH` edges from assets decided in pass 0 |
| Visited set | Per-pipeline-run set of `AssetId`s already decided; prevents cycle re-entry |
| `propagation_depth` | Integer on `PredictionMade`: `0` = direct (CausalFactor→Asset), `1+` = hop count through `CORRELATES_WITH` chain |
| `propagation_chain` | Ordered list of `PropagationHop` objects on `PredictionMade`; empty for direct predictions |
| `PropagationHop` | Value model: `(source_asset_id, target_asset_id, condition, direction, edge_weight)` |
```

### 2. Section 9.2 — Neo4j graph model: add new edge type

Find the graph model block that currently reads:

```text
(:CausalFactor {id})-[:CAUSES {condition, direction, weight, confidence,
                               alpha, beta, last_updated}]->(:Asset {id})

(:CausalFactor {id})-[:CAUSES {...}]->(:AssetGroup {id})    -- industry-level, inherited

(:Asset {id})-[:MEMBER_OF]->(:AssetGroup {id})
```

Add after the last line:

```text
(:Asset {id})-[:CORRELATES_WITH {condition, direction, weight, confidence,
                                  alpha, beta, last_updated}]->(:Asset {id})
              -- condition is always set (UPSTREAM_UP or UPSTREAM_DOWN); no unconditional form
              -- fires during Prediction's propagation pass when source asset predicted directional
              -- alpha/beta learned by Credibility (online) and offline structure learner
```

### 3. Section 4 — End-to-end flow, Step 3 (Prediction): extend description

Find the paragraph describing the Prediction step and add a sentence after the existing
force-summation description:

```
After pass 0, a propagation pass queries CORRELATES_WITH edges for each directionally
predicted asset and runs decide() for downstream targets, up to MAX_PROPAGATION_DEPTH hops.
A visited set per pipeline run prevents cycles.
```

### 4. Section 8 — Canonical messages: add new fields to PredictionMade row

Find the `PredictionMade` row in the canonical messages table and update its description:

```markdown
| `PredictionMade` | Prediction | One prediction with contributing edges, decision method, `propagation_depth` (0 = direct), and `propagation_chain` (list of `PropagationHop`; empty for direct) | [SRS-04 §8](SRS-04-prediction.md#8-interfaces) |
```

### 5. Bump version and last-verified date

Change `Version` to `1.3.0` and `Last verified against code` to `2026-08-07`.

## Acceptance criteria

1. All six new definitions present in section 3.
2. `CORRELATES_WITH` edge type present in section 9.2 with correct properties listed.
3. Propagation pass described in the step 3 narrative.
4. `PredictionMade` row in section 8 references `propagation_depth` and `propagation_chain`.
5. Version bumped to `1.3.0`.
6. All relative links still resolve.
7. No existing definition, requirement, or diagram text removed.

## Definition of done

- [ ] Section 3 definitions updated
- [ ] Section 9.2 graph model updated
- [ ] Section 4 step 3 narrative updated
- [ ] Section 8 `PredictionMade` row updated
- [ ] Version bumped
- [ ] All relative links verified
