# T03 — Update SRS-07 (Credibility Service) with Correlation-Edge Learning

## Context

`requirements/SRS-07-credibility.md` specifies the Credibility Service. It currently
describes only the `CAUSES`-edge Beta-Bernoulli update path. After S04, the service also
updates `CORRELATES_WITH` edges when a propagated prediction is scored.

Read `requirements/SRS-07-credibility.md` in full before editing.

## Changes required

### 1. Responsibilities / what-it-does section — add both correlation paths

Find the existing description of the Beta-Bernoulli update and add two parallel bullets:

```
- ONLINE: When PredictionScored.propagation_chain is non-empty, update the Beta-Bernoulli
  counts (alpha/beta) on the CORRELATES_WITH edge identified by the last hop in the chain
  (source_asset_id, target_asset_id, condition). The correct hop is always the last because
  each independently scored PredictionMade was produced by exactly one CORRELATES_WITH edge.

- OFFLINE: The structure learner (python -m credibility.learning.run) runs a second data
  path alongside the CAUSES learner. It reads historical scored direct predictions
  (propagation_depth=0) from prediction.predictions joined to verification.scores, looks up
  the target asset's actual return in the same settlement session from
  market_data.close_observations, and upserts CORRELATES_WITH edges via MERGE — refining
  expert-seeded edges and discovering new ones when the data supports them.
```

### 2. Key definitions — add new terms

```markdown
| CORRELATES_WITH edge learning | Beta-Bernoulli update on (:Asset)-[:CORRELATES_WITH]->(:Asset) edges, triggered when a propagated prediction is scored |
| propagation_chain | List of PropagationHop objects on PredictionMade identifying the CORRELATES_WITH edges that produced the prediction |
| Last hop | The final PropagationHop in propagation_chain; its (source, target, condition) identifies the edge that directly produced the scored prediction |
```

### 3. Functional requirements — add new requirements

Find the next available requirement number in SRS-07 and add:

```
CRD-N  (Must / Implemented)  When processing a PredictionScored whose originating
       PredictionMade carries a non-empty propagation_chain, the service shall update
       the CORRELATES_WITH edge identified by the last hop in the chain using the same
       Beta-Bernoulli increment rule (alpha += 1 if correct, beta += 1 if wrong).

CRD-N+1 (Must / Implemented) When processing a PredictionScored whose originating
        PredictionMade carries an empty propagation_chain (direct prediction), the service
        shall follow the existing CAUSES-edge update path unchanged.

CRD-N+2 (Must / Implemented) The service shall never update a CAUSES edge when the
        scored prediction has a non-empty propagation_chain, and shall never update a
        CORRELATES_WITH edge when the chain is empty.
```

### 4. Update document status and version

Bump the document version to `1.1.0` and update `last verified against code` to `2026-08-07`.

## Acceptance criteria

1. Correlation-edge learning described in the responsibilities section.
2. Three new key definitions present.
3. Three new `CRD-*` requirements added with status `Implemented`.
4. Document version bumped.
5. No existing requirement text removed or renumbered.
6. All relative links still resolve.

## Definition of done

- [ ] Responsibilities section updated
- [ ] Three new key definitions added
- [ ] Three new `CRD-*` requirements added
- [ ] Document version bumped
- [ ] All relative links verified
