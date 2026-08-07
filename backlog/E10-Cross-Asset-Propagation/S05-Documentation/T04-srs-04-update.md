# T02 — Update SRS-04 (Prediction Service) with Propagation Requirements

## Context

`requirements/SRS-04-prediction.md` specifies the Prediction Service. It currently describes
the single-pass graph traversal only. After S03, the service runs a multi-pass propagation
loop. The specification must reflect this so the requirement status can move from `Approved`
to `Implemented` for the new behaviour.

Read `requirements/SRS-04-prediction.md` in full before editing. Identify the exact sections
that need updating and edit only those.

## Changes required

### 1. Section 2.1 (Responsibilities) — add propagation

Add to the numbered responsibility list:

```
6. Cross-asset propagation — for each asset that received a directional prediction in pass 0,
   query CORRELATES_WITH edges active under the matching condition (UPSTREAM_UP / UPSTREAM_DOWN)
   and run force summation for downstream targets. Repeat up to MAX_PROPAGATION_DEPTH passes.
   A visited set per pipeline run prevents re-predicting an asset that was already decided.
```

### 2. Section 2.2 (What it does NOT do) — no change needed

The existing "no LLM calls" and "does not fetch prices" bullets remain accurate.

### 3. Section 3 (Key definitions) — add new terms

```markdown
| Propagation pass | A subsequent decide() sweep over assets reachable via CORRELATES_WITH edges from assets decided in the previous pass |
| Visited set      | Per-pipeline-run set of AssetIds already decided; prevents cycles and duplicate predictions |
| UPSTREAM_UP / UPSTREAM_DOWN | ConditionCode values that gate a CORRELATES_WITH edge based on the upstream asset's predicted direction |
| PropagationHop   | Value model recording one fired CORRELATES_WITH edge: (source, target, condition, direction, weight) |
| propagation_depth | Integer on PredictionMade: 0 for direct, 1+ for propagated; counts the number of CORRELATES_WITH hops |
| MAX_PROPAGATION_DEPTH | Config ceiling on hop count (default 3, range 1–10) |
```

### 4. Functional requirements section — add new requirements

Find the next available requirement number in SRS-04 and add:

```
PRD-N  (Must / Implemented)  The service shall run a propagation pass after each direct
       prediction pass, querying CORRELATES_WITH edges for each directionally predicted asset
       and running decide() for downstream targets, up to MAX_PROPAGATION_DEPTH passes.

PRD-N+1 (Must / Implemented) The service shall maintain a visited set per pipeline run;
        an asset already decided in any pass shall not be decided again in the same run.

PRD-N+2 (Must / Implemented) Each propagated PredictionMade shall carry propagation_depth >= 1
        and a propagation_chain listing every CORRELATES_WITH edge that contributed to it.

PRD-N+3 (Must / Implemented) A direct PredictionMade shall carry propagation_depth == 0 and
        an empty propagation_chain (backward-compatible default).
```

### 5. Update document status and version

Bump the document version to `1.1.0` and update `last verified against code` to `2026-08-07`.

## Acceptance criteria

1. All six new key definitions are present in section 3.
2. Four new `PRD-*` requirements are added with status `Implemented`.
3. Responsibility list in section 2.1 includes propagation as item 6.
4. Document version bumped.
5. No existing requirement text removed or renumbered.
6. All relative links still resolve.

## Definition of done

- [ ] Section 2.1 updated
- [ ] Section 3 key definitions updated
- [ ] Four new `PRD-*` requirements added
- [ ] Document version bumped
- [ ] All relative links verified
