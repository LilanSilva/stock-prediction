# POC-3: Prediction Architecture Selection

**Status:** DONE - SUPERSEDED FOR M1 BY POC-6  
**Original date:** 2026-06-25  
**Superseded by:** [POC-6 controlled rerun](poc-6-end-to-end-prediction-validation.md)

## Original Finding

POC-3 selected a Knowledge Graph plus LLM-arbiter architecture because it appeared to support compound/conflicting multi-event reasoning without historical training data.

The original hypothesis was:

- Use the knowledge graph to ground causal forces.
- Originally proposed: use an LLM arbiter to decide which force dominates when graph forces conflict.
- Store structured prediction output with full provenance.

## POC-6 Update

POC-6 tested that hypothesis on 30 frozen conflict contexts. The controlled rerun recorded `STOP`: KG-plus-LLM arbitration did not improve enough over graph-only to justify implementation in M1.

Measured POC-6 result:

- Graph-only accuracy: 40.0%.
- KG-plus-LLM accuracy: 36.7%.
- Paired evidence: 2 corrected, 3 harmed, net -1.
- Bedrock calls: 40.
- Actual list-price cost: USD 0.082641.

## Current M1 Decision

Use Knowledge Graph / graph-only prediction for M1.

Do not implement prediction-time LLM arbitration in M1.

The shared LLM gateway remains useful for Cleansing Service ambiguous extraction/factual-conflict resolution and for future approved experiments.

## Future Option

LLM arbitration may be reopened only if a new controlled hypothesis is approved. Any future implementation must:

- call only through the shared provider-configurable LLM gateway;
- select provider/model via `LLM_PROVIDER` and `LLM_MODEL`;
- use provider-specific API keys or approved cloud credentials from configuration;
- send compact structured context and graph paths, not full articles;
- cache by provider, model, prompt version, schema hash, and context hash;
- record provider usage metadata without extra LLM calls.

## Relevant Tasks

- [SRS-04 — Prediction Service](../../requirements/SRS-04-prediction.md) (KG schema, Cypher queries, commodity seeding, event-to-graph matcher)
- Deferred: LLM conflict arbiter — see [SRS-04 §13](../../requirements/SRS-04-prediction.md#13-assumptions-and-limitations) (blocked by POC-6 STOP result)
