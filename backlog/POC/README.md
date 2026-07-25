# POC Findings

All proof-of-concept research and decisions made before development started.
Each file documents what was investigated, what was found, what was decided, and why, with links to tasks that implement those decisions.

**Read these before starting any task.** They explain the "why" behind technical choices that would otherwise look arbitrary.

| File | What it covers | Most important finding |
|---|---|---|
| [poc-1-news-api-source-selection.md](poc-1-news-api-source-selection.md) | Which free news APIs are actually real-time | NewsAPI/GNews/NewsData all delay 12-24h on free tier; DI, DN, Aftonbladet, SvD RSS, and GDELT were validated for the POC source path |
| [poc-2-news-cleansing-approach.md](poc-2-news-cleansing-approach.md) | How to deduplicate without over-merging distinct events | Over-merge is fatal. Dual-gate clustering, cosine >= 0.80 plus action signature match, is the solution |
| [poc-3-prediction-architecture.md](poc-3-prediction-architecture.md) | Knowledge graph vs trained model vs rules | Done; superseded for M1 by POC-6: use graph-only prediction; LLM arbitration is deferred |
| [poc-4-market-data-verification.md](poc-4-market-data-verification.md) | Price data source and scoring math | POC-6 supersedes provider selection; Yahoo provider reference closes are approved for POC scoring, Stooq remains unvalidated |
| [poc-5-existing-tools-landscape.md](poc-5-existing-tools-landscape.md) | Does this product already exist? | Two confirmed gaps: compound conflict resolution and self-improving credibility |
| [poc-6-end-to-end-prediction-validation.md](poc-6-end-to-end-prediction-validation.md) | Does KG-plus-LLM beat simpler baselines within a fixed token budget? | `STOP`: controlled rerun found KG-plus-LLM underperformed graph-only on the 30-context sample |
| [P06 validation remediation](P06-Validation-Remediation/README.md) | Repairs the POC-6 dataset and reference-price gates before a controlled rerun | Complete: frozen corpus, reference-close policy, capped Bedrock run, and paired metric decision recorded |
