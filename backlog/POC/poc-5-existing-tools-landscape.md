# POC-5: Existing Tools Landscape Research

**Status:** COMPLETE — market research done
**Date:** 2026-06-25
**Outcome:** PASSED — two genuine gaps confirmed, product thesis validated

---

## What Was Investigated

Whether tools like this already exist commercially or open-source, and if not, why not. Researched 2024-2026 commercial products, open-source projects, and academic literature.

---

## Commercial Tools (what exists today)

### RavenPack (market leader)
- Ingests 40,000+ sources in 13 languages, 12M+ entities, 7,000+ event categories
- Produces relevance, novelty, and sentiment impact scores
- Used by quant hedge funds and major banks
- Pricing: enterprise-only, undisclosed (very expensive)
- **Gap: no compound/conflict resolution. Aggregates sentiment scores but does not model directional conflict between concurrent causal chains.**

### MarketPsych (via LSEG/Reuters)
- Behavioral/psychological angle: fear, optimism, war/violence signals
- Distributed via LSEG Data feeds and Databricks
- Has a Radar MCP integration with Claude AI
- **Gap: same as RavenPack — single-signal sentiment scoring per asset**

### Bloomberg Terminal ($24,000+/user/year)
- Comprehensive real-time data, but sentiment is a secondary feature
- News search and alerting, not predictive signals
- **Gap: not a prediction engine**

### LSEG/Refinitiv Workspace
- 400,000+ users, 190 markets, bundles MarketPsych sentiment
- **Gap: same single-signal limitation**

### Kensho (S&P Global)
- LLM-ready API for connecting AI to S&P datasets
- Partners with Anthropic, OpenAI, Google Cloud
- Oriented toward structuring proprietary data, not ready-made signals

---

## What None of Them Do

**Confirmed gap 1: Compound multi-event conflict resolution**
No commercial product explicitly handles when multiple simultaneous news events push an asset in conflicting directions. They aggregate sentiment scores but do not reason about which force dominates.

Classical finance event studies (Warren & Sorescu 2017) actually **exclude** periods with concurrent news to keep their analysis clean — confirming this is not a solved problem even in academia.

**Confirmed gap 2: Self-improving credibility scoring**
No commercial tool publicly discloses tracking which past predictions were correct and reweighting future predictions accordingly. Likely exists internally as proprietary advantage but not a feature.

---

## Open-Source Projects

| Project | Stars | What it does | Compound conflict? |
|---|---|---|---|
| FinGPT (AI4Finance) | 20.7k | LLM fine-tuned for sentiment + Dow30 forecasting | No |
| FinRL (AI4Finance) | 15.5k | RL trading framework, 14+ data sources | No |
| FinRobot (AI4Finance) | 7.4k | Multi-agent equity research reports | No |
| FinBERT (Prosus) | 7.5M HF downloads/mo | BERT for financial sentiment (pos/neg/neutral) | No |

**None handle compound multi-event reasoning.** All are single-signal sentiment classifiers or RL trading environments.

---

## Academic Research (2024–2026 frontier)

| Paper | What it does | Relevant to this product |
|---|---|---|
| CausalStock (NeurIPS 2024, 2411.06391) | Causal graph discovery + FCM + LLM denoiser | ✅ Architecture validation |
| CAMEF (SIGKDD 2025, 2502.04592) | LLM counterfactual augmentation for macro news | ✅ Cold-start data bootstrapping |
| TradExpert (2411.00782) | 4 specialist LLMs + General Expert arbiter | ✅ LLM-as-arbiter pattern |
| CSHT (2510.04357) | Causal Sphere Hypergraph Transformer | ✅ Multi-event attribution |
| EventConnector (2606.15448) | Temporal social event graphs | Related |
| RAVEN (2606.24062) | Mixture-of-Experts with Correlation-Aware Weighting | Related |

**Pattern:** academia is formalizing the compound-event problem (2023–2026) but none of this has reached commercial deployment. The product is at the frontier.

---

## Why This Space Is Not Democratized

Five compounding barriers:
1. **Data cost:** Licensed news feeds (Reuters, Dow Jones) are expensive. Real-time low-latency adds more.
2. **Compute:** Running NLP at 40,000+ sources requires serious infrastructure.
3. **Domain complexity:** Financial NLP requires specialized training data not available in public corpora at quality.
4. **Licensing:** Best financial LLMs (BloombergGPT) are proprietary. Open models lag.
5. **Regulatory:** Anything resembling a financial prediction product attracts compliance overhead.

**Why this POC avoids those barriers:**
- Free news sources (GDELT + RSS) sidestep data cost
- LLM-based reasoning (Claude API) sidesteps specialized training data
- Starting with commodities/indices (not individual stock recommendations) reduces regulatory exposure
- Zero compute cost at POC scale

---

## Implications for Product Strategy

- **The compound-conflict differentiator is real and unaddressed** — not a gap in the research, genuinely unsolved in commercial products
- Start with **commodities (gold, oil)** where geopolitical compound effects are clearest and publicly observable — not Swedish equities first
- The self-improving credibility loop is a **moat** — it gets better with data while competitors' static sentiment scores don't
- **Phase 3 trained model** (from POC-3) is the long-term competitive advantage — as labeled data accumulates, the system improves beyond what any rule-based competitor can match

---

## Relevant Tasks
This POC informs the overall architecture rather than specific tasks. Most directly relevant:
- [E04/S01/T02 — Seed initial commodity KG](../E04-Prediction-Service/S01-Knowledge-Graph-Setup-Seeding/T02-seed-initial-commodity-knowledge-graph.md) — commodity focus validated here
- [E07 — Credibility Service](../E07-Credibility-Service/README.md) — the self-improving loop confirmed as a gap
