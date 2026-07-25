# Core Acceptance Map

This is a lightweight map from the product's essential behaviours to executable acceptance scenarios. It replaces a formal FR/NFR/BR traceability matrix.

| Core behaviour | Acceptance scenario |
|---|---|
| Preserve distinct causal events | Military conflict and strait closure remain separate after Cleansing even when semantically similar |
| Cross-language clustering | Swedish and English reports of the same rate decision join one cluster through local taxonomy normalization |
| Multi-event reasoning | Two distinct events in one context produce one asset prediction containing both event IDs and opposing paths |
| Token-efficient inference | An uncontested graph result produces `decision_method=GRAPH_ONLY` and no LLM metadata |
| Bounded LLM use | M1 prediction produces graph-only output with zero LLM calls; any future approved arbitration experiment records returned token usage |
| Canonical asset identity | `GOLD` crosses service boundaries while `GC=F` remains inside the yfinance adapter |
| Close-to-close scoring | Known approved reference closes with matching registry/price-kind metadata calculate the exact return and deadband direction |
| Independent fan-out | Verification and Gateway both receive a prediction without competing for the same queue message |
| Restart recovery | A persisted future price request is executed after Market Data restarts |
| Learning idempotency | Replaying one `PredictionScored` message does not change alpha/beta a second time |
| Directional edge learning | An edge voting against the observed direction is not rewarded because the final arbiter decision was correct |
| Local-only POC security | Article fetching rejects private/metadata destinations and API exposure is limited to the configured local boundary |
