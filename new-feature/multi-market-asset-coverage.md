# Feature Requirement: Multi-Market Asset Coverage (Nordic / European listings)

Status: Proposed (not scheduled). Captured 2026-08-02.

## Motivation

The system ingests Swedish RSS news alongside English sources, but every tradeable asset is currently
priced by a **US** large-cap bellwether via `biquote.io` (e.g. `DEFENSE_AEROSPACE -> LMT`). Swedish
news (e.g. about SAAB, Ericsson, Volvo) is most relevant to **Swedish-listed** equities, which the
system cannot currently represent or price. This feature adds coverage for non-US markets
(Stockholm / Euronext) so that local news can drive local-market predictions.

## Idea that prompted this

Use exchange-qualified, multi-listing symbols per asset, e.g.:

```
DEFENSE_AEROSPACE : NASDAQ:LMT / STO:SAAB-B / EPA:AIR
```

(first part = exchange/market code, second part = company ticker).

## Findings (verified 2026-08-02 against biquote.io)

Blocking constraints discovered while probing the current provider:

- **biquote serves US mega-caps only.** Bare US tickers work (LMT, TSM, NVDA, GOOGL, US500);
  Swedish/European listings return **0 bars**: `ERIC`, `SAAB-B`, `VOLV-B`, `NDA-SE`, `INVE-B`,
  `AIR`, `AIR.PA` all empty. Even Ericsson's US ADR (`ERIC`) returned 0.
- **biquote does NOT accept an `EXCHANGE:TICKER` format.** Every prefixed form returned 0 bars:
  `NASDAQ:LMT`, `STO:SAAB-B`, `EPA:AIR`, `LMT.US`. Only bare symbols are accepted.

Conclusion: the `EXCHANGE:TICKER` multi-listing idea is not feasible with the current provider and
requires a new/additional price data source.

## Design decision: separate per-market assets, NOT multiple tickers per asset

One asset must map to exactly one price series. `LMT` (USD), `SAAB-B` (SEK), and `AIR` (EUR) are
three different companies, prices, and currencies; a single prediction/score cannot be evaluated
against three instruments at once. Combining listings into one asset breaks scoring and learning.

Therefore, model each market as its own canonical asset with its own price series, edges, and
scoring, for example:

```
DEFENSE_AEROSPACE_US -> LMT    (USD, biquote)
DEFENSE_AEROSPACE_SE -> SAAB-B (SEK, new provider)
DEFENSE_AEROSPACE_EU -> AIR    (EUR, new provider)
```

The knowledge-graph and prediction model already support additional assets; the missing pieces are
the price provider and a provider-aware registry.

## Requirements

1. **New price provider (Market Data adapter).** Add an adapter for a data source that serves
   Stockholm (`STO`) and/or Euronext (`EPA`, etc.) daily closes. Candidates to evaluate: Stooq
   (revisit; previously disabled), an EOD/vendor API, or a broker feed. Must satisfy the existing
   adapter contract (`get_close`, `fetch_observations`, PriceNotYetAvailable / InvalidObservation /
   AdapterUnavailable) and the include-all rollover + `PROVIDER_DAILY_CLOSE` policy.
2. **Provider-aware asset registry.** Extend `shared/reference/asset_registry.py` so each asset
   declares its `provider` (not just a bare `provider_symbol`), enabling per-asset provider routing.
   Keep provider symbols out of business messages (canonical `AssetId` only at boundaries).
3. **Per-market canonical assets.** Add `*_SE` / `*_EU` `AssetId` values and seed matching
   `(:Asset)` nodes with a market/currency attribute.
4. **Cleansing asset inference.** Map local company/market keywords (e.g. "SAAB", "Ericsson",
   "Volvo") to the corresponding per-market assets.
5. **Market Data routing.** Dispatch a price request to the correct provider adapter based on the
   asset's registry entry.
6. **Currency awareness.** Record the price currency per asset. (Close-to-close direction is
   currency-agnostic within one instrument, so no FX conversion is required for direction scoring;
   still store currency for correctness and any future magnitude/normalization work.)

## Non-goals / constraints

- Do NOT combine multiple listings into one asset (breaks single-price-series scoring).
- Do NOT introduce prediction-time LLM calls (POC-6 `STOP` remains in force).
- Keep the change additive and backward-compatible: existing US bellwether assets are unaffected.
- No new always-on service; the offline structure learner still discovers edges for new assets.

## Open questions

- Which provider reliably serves STO/Euronext daily closes within the POC's cost/rate limits?
- Session-calendar handling for non-US exchanges (holidays, trading hours) vs the current
  America/New_York 17:00 completion model in `shared.calendar`.
- Symbol conventions and corporate-action/adjustment handling for Nordic tickers (e.g. `SAAB-B`).

## Acceptance criteria (when scheduled)

- At least one non-US market asset (e.g. `DEFENSE_AEROSPACE_SE`) is defined, seeded, priced by the
  new provider, and produces a scorable prediction end to end.
- Registry resolves each asset to the correct `(provider, symbol)` pair.
- Existing US assets continue to resolve and score unchanged.
- Shared/service test suites remain green (ruff, mypy --strict, pytest).
