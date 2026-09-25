# E15 — Parallel KG and trained-model prediction

Status: Approved on 2026-09-25; implementation in progress. The first evidence-capture delivery is implemented and tested locally. No model, training schedule or production replacement is activated.

Build a repeatable comparison of the existing knowledge graph and supervised models inside Prediction. Train models offline, evaluate on the same future outcomes, and replace the official predictor only when a candidate meets the documented gates. The objective is measurable improvement with honest uncertainty, not perfect market prediction.

| File | Contains |
|---|---|
| [Development plan](development-plan.md) | Completed-work checklist and remaining training, dataset, comparison, replacement, contract and acceptance work; each phase has an explicit status |

Dependencies: [Prediction](../../requirements/SRS-04-prediction.md), [Verification](../../requirements/SRS-06-verification.md), [Credibility](../../requirements/SRS-07-credibility.md), and [E14 price snapshots](../E14-Avanza-Price-Snapshots/README.md). E14 sampled observations are an optional evidence source, not a substitute for continuous intraday bars.

Development follows the phases in the plan. Completing a phase does not establish forecasting skill; production promotion requires the separate evidence gate. Implementation must update the owning SRS and contracts as each behavior is delivered.

Delivered functionality and its architecture are documented in the
[Prediction service README](../../src/services/prediction/README.md), with requirements and tests in
[SRS-04](../../requirements/SRS-04-prediction.md#712-research-evidence-capture).
The delivery-status checklist marks implemented work **Done** and lists the remaining scope.
