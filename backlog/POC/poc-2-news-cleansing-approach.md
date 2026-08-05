# POC-2: News Cleansing Approach

**Status:** COMPLETE — approach decided, not yet code-implemented
**Date:** 2026-06-25
**Outcome:** PASSED — architecture decided with critical design constraint identified

---

## What Was Investigated

How to deduplicate and cluster news articles from multiple sources that report the same event in different ways, while preserving distinct events that happen to share entities or topics.

---

## The Critical Finding: Two Failure Modes, One Is Fatal

Standard dedup tools optimize for merging similar content. For this product that is **wrong**.

| Failure mode | Cause | Consequence |
|---|---|---|
| **Under-merge** (same event stays split) | Threshold too loose | Iran war reported by 5 channels = 5× bullish oil signals → over-weighting |
| **Over-merge** (distinct events collapsed) | Topic-only clustering | Iran war + Hormuz block become ONE event → opposing forces vanish → **product differentiator destroyed** |

**Over-merge is the more dangerous error.** Standard tools optimize for the opposite.

### The Iran/Hormuz example
- "Iran launches military strike" and "Iran closes Hormuz strait" share: actor (Iran), geography (Middle East/Gulf), entities (oil, gold, USD).
- A topic-similarity clusterer would merge them — same subject matter.
- But they are **two distinct causal forces** on gold: war = gold UP (safe haven), Hormuz closure = USD UP = gold DOWN.
- Merging them silently destroys the compound signal before reasoning even starts.

---

## Decision: Conservative Dual-Gate Clustering

Articles merge into one event **ONLY IF BOTH gates pass**:

**Gate 1 — Semantic similarity:** cosine similarity of BGE-m3 embeddings ≥ 0.80
**Gate 2 — Action signature compatibility:** extracted (actor, action, object) triples must share the same action verb lemma or a known synonym

**When gates disagree → always keep events separate.**

### Why gate 2 catches the Iran/Hormuz case
- "Iran launches strike" → action = `launch`
- "Iran closes strait" → action = `close`
- Gate 1: both about Iran + Gulf + oil → similarity likely ≥ 0.80 → would merge
- Gate 2: `launch` ≠ `close` → blocks the merge ✓

---

## Architecture Decision: 4-Stage Funnel

Processing ~500 raw articles/hr down to ~40 structured events:

```
~500 raw articles
   │  Stage 1: Normalize & filter (rules)         → ~400
   │  Stage 2: SimHash near-dup collapse           → ~300
   │  Stage 3: BGE-m3 embed + dual-gate cluster    → ~40 clusters
   │  Stage 4: LLM merge per cluster (1 call each) → ~40 events
   ▼
Hourly structured event set
```

Spend LLM tokens only on ~40 cluster representatives, not 500 raw articles.

---

## Technology Decisions

| Decision | Choice | Reason |
|---|---|---|
| Embeddings | **BGE-m3** (BAAI/bge-m3) via sentence-transformers | Multilingual — Swedish + English in one vector space. Free, local, no per-call cost. 1024-dim. |
| Embeddings runtime | **Local** (not hosted API) | Zero cost, confirmed by user preference |
| Near-dup | **datasketch MinHash / SimHash** | Cheap, kills wire-syndication copies before spending embedding compute |
| Action extraction | **spaCy** (en_core_web_sm + sv_core_news_sm) | Free, handles both languages, extracts subject/verb/object |
| Vector store | **Postgres + pgvector** (`vector(1024)` column) | Eliminates Qdrant as a separate engine — one less infra component |
| Clustering policy | **Conservative** (when in doubt: keep separate) | Confirmed by user — under-merge is recoverable, over-merge is fatal |

---

## Evaluation Approach (for implementation validation)

Build a labeled test set of ~100 articles from real hours, hand-grouped by true event. Seed it with deliberate traps (Iran-war + Hormuz-block in the same hour). Measure:

- **Over-merge rate** → primary metric, drive toward 0%
- Standard precision/recall on cluster assignments

---

## Implications for Implementation

- The cosine threshold (0.80) and action-signature matching are **not arbitrary** — they are the product's safety mechanism. Do not loosen without re-running the labeled eval set.
- BGE-m3 model must load at **service startup** and stay in memory — do not reload per request (startup is ~10s, inference is ~50ms/batch).
- Batch embedding in groups of 32 for throughput.
- Cluster **expiry window = 24h** — after 24h with no new articles, a cluster closes and its LLM merge is triggered even with 1 article.
- The LLM merge prompt must explicitly instruct: "preserve all specific numbers, names, dates from the most detailed source" and "flag disagreements between sources as fact_conflicts."

---

## Relevant Tasks
- [SRS-03 — Cleansing Service](../../requirements/SRS-03-cleansing.md) (SimHash dedup, action signature extractor, BGE-m3 embedding, dual-gate clustering, LLM event merge)
