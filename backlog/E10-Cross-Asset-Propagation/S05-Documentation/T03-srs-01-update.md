# T03 — Update SRS-01 (Shared Foundation)

## Context

`requirements/SRS-01-shared-foundation.md` specifies everything in `src/shared/`. E10 adds
two new `ConditionCode` values and two new fields on `PredictionMade`, plus a new
`PropagationHop` model and `CorrelationEdge` graph model. All of these live in `src/shared/`
and must be reflected in SRS-01.

Read the full file before editing.

## Changes required

### 1. Section 8.2 — Shared enums: extend `ConditionCode`

Find the `ConditionCode` row in the shared enums table:

```markdown
| `ConditionCode` | `TRANSPORT_AFFECTED`, `SAFE_HAVEN_ONLY`, `RISK_PREMIUM_ELEVATED` |
```

Replace with:

```markdown
| `ConditionCode` | `TRANSPORT_AFFECTED`, `SAFE_HAVEN_ONLY`, `RISK_PREMIUM_ELEVATED`, `UPSTREAM_UP`, `UPSTREAM_DOWN` |
```

### 2. Section 5.1 — Functional requirements: update SHR-9

Find `SHR-9` which lists all shared enums. `ConditionCode` is already listed — no change to
the requirement text needed if it references `ConditionCode` by name rather than listing
values. If the requirement text enumerates values, add `UPSTREAM_UP` and `UPSTREAM_DOWN`.

### 3. Section 8 — `PredictionMade` fields: add propagation fields

Find the `PredictionMade` message definition (section 8 or wherever its fields are listed)
and add:

```markdown
| `propagation_depth` | `int` | `0` | `0` = direct (CausalFactor→Asset), `1+` = hop count through CORRELATES_WITH chain |
| `propagation_chain` | `list[PropagationHop]` | `[]` | Ordered list of fired CORRELATES_WITH edges; empty for direct predictions; backward-compatible default |
```

### 4. Add `PropagationHop` model description

Find the section listing shared nested types (e.g. `ContributingEdge`, `CloseObservation`)
and add:

```markdown
| `PropagationHop` | Frozen value model: `source_asset_id`, `target_asset_id`, `condition` (ConditionCode), `direction` (Direction), `edge_weight` (float). Carried in `PredictionMade.propagation_chain`. |
```

### 5. Add `CorrelationEdge` graph model description

Find the section describing `FiringEdge` (the shared graph model) and add alongside it:

```markdown
| `CorrelationEdge` | Frozen value model returned by `CausalGraphClient.get_correlation_edges()`: `source_asset_id`, `target_asset_id`, `condition`, `direction`, `weight`, `confidence`, `alpha`, `beta`. `reliability = alpha / (alpha + beta)`. `edge_id = "SOURCE|CONDITION->TARGET"`. |
```

### 6. Bump version and last-verified date

Change `Version` to `1.2.0` and `Last verified against code` to `2026-08-07`.

## Acceptance criteria

1. `ConditionCode` enum row includes `UPSTREAM_UP` and `UPSTREAM_DOWN`.
2. `PredictionMade` field table includes `propagation_depth` and `propagation_chain`.
3. `PropagationHop` model described alongside other nested types.
4. `CorrelationEdge` model described alongside `FiringEdge`.
5. Version bumped to `1.2.0`.
6. All relative links still resolve.
7. No existing requirement removed or renumbered.

## Definition of done

- [ ] `ConditionCode` enum updated in §8.2
- [ ] `SHR-9` verified (update if it enumerates values)
- [ ] `PredictionMade` propagation fields documented
- [ ] `PropagationHop` model documented
- [ ] `CorrelationEdge` model documented
- [ ] Version bumped
- [ ] All relative links verified
