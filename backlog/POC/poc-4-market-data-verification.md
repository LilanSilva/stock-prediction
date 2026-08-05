# POC-4: Market Data & Verification Approach

**Status:** COMPLETE — approach decided
**Date:** 2026-06-25
**Outcome:** PASSED — yfinance + Beta-Bernoulli scoring approach confirmed

> **POC-6 revalidation (2026-07-13):** Yahoo daily data was available for `GC=F` and `BZ=F`, but `BZ=F` reported NYM/America_New_York metadata rather than the registry's ICE-Europe/Europe-London identity. Stooq returned non-CSV responses for both proposed fallback symbols, and no continuous-futures rollover policy exists. The provider selection part of this POC is superseded: Yahoo remains a candidate research reference source, Stooq is unvalidated, and neither asset may be scored until P06/T03 freezes session and rollover policy. The scoring mathematics remains accepted.

---

## What Was Investigated

Two things: (1) which free price data source to use for verification, and (2) the correct mathematical approach for scoring predictions and updating credibility weights.

---

## Part A: Price Data Source

### Why Not Google Finance API
There is **no official Google Finance API**. Google shut it down in 2012.
- `GOOGLEFINANCE()` in Google Sheets is a spreadsheet formula only — not callable from Python
- Anything labeled "Google Finance API" on the web is an unofficial scraper — fragile and against ToS
- **Do not build on this**

### Decision: yfinance (primary) + Stooq (fallback)

**yfinance:**
- Free, no API key, no registration
- Covers: gold (GC=F), brent oil (BZ=F), USD index (DX-Y.NYB), US equities, Swedish equities (.ST tickers e.g. ERIC-B.ST)
- Returns daily OHLC + intraday
- Near-real-time daily closes (15-min delay on quotes, not relevant for close-to-close verification)
- Known issue: occasional missing data for Swedish tickers — handle with fallback

**Stooq (fallback):**
- Free, no API key, via pandas-datareader
- Different symbol format: ^GOLD, ^OIL, ERIC-B.PL for Swedish
- Use only when yfinance fails 3× with exponential backoff

### Asset Symbol Mapping

| Asset | yfinance | Stooq |
|---|---|---|
| Gold | GC=F | ^GOLD |
| Brent Oil | BZ=F | ^OIL |
| USD Index | DX-Y.NYB | — |
| OMXS30 | ^OMX | ^OMX |
| Swedish equity | ERIC-B.ST | ERIC-B.PL |
| S&P 500 | SPY | ^SPX |

---

## Part B: Scoring & Weight Update Methodology

### Defining "Correct"

**Method: close-to-close with deadband**

```
actual_return = (close_at_window - close_at_prediction_time) / close_at_prediction_time

if abs(actual_return) < 0.003:  # 0.3% deadband
    actual_direction = NEUTRAL
elif actual_return > 0:
    actual_direction = UP
else:
    actual_direction = DOWN

is_correct = (predicted_direction == actual_direction)
```

**Why 0.3% deadband:** tiny moves are noise — a prediction of "UP" on a 0.1% move is not meaningful. The deadband prevents false positives from market microstructure noise.

**Why close-to-close:** intraday high/low is too noisy (touched briefly). Weekly is too long (too many confounders). 1 trading day close-to-close is the best signal/noise tradeoff at POC scale.

### Weight Update: Beta-Bernoulli (Bayesian)

**Why not naive +1/−1:** oscillates and never converges.
**Why not exponential moving average alone:** doesn't provide confidence intervals.

**Beta-Bernoulli:**
```python
# Prior: alpha=1, beta=1 (uninformed — 50/50 before any evidence)
# On correct prediction: alpha += credit
# On wrong prediction:   beta += credit
# Credibility = alpha / (alpha + beta)
# 95% CI = scipy.stats.beta.interval(0.95, alpha, beta)
```

**Concrete example:**
- New edge (alpha=1, beta=1): credibility=0.50, CI=(0.03, 0.97) — wide, uncertain
- After 10 hits, 2 misses (alpha=11, beta=3): credibility=0.786, CI=(0.49, 0.95)
- After 100 hits, 40 misses (alpha=101, beta=41): credibility=0.711, CI=(0.62, 0.79) — tight, trustworthy

The confidence interval is the protection against overconfidence from lucky early streaks.
**One lucky hit on a new edge does NOT spike its weight** — the wide CI shows it's still uncertain.

### Proportional Credit Assignment

When multiple edges contributed to a prediction:
```
credit_i = influence_weight_i / sum(all_influence_weights)
```

If edge A had influence 0.8 and edge B had influence 0.2:
- Edge A gets 80% of the credit/blame
- Edge B gets 20%

This is proportional, not equal. Equal split would incorrectly blame a low-influence edge as much as a high-influence one.

---

## Implications for Implementation

- **yfinance wrapper must be async** — use `asyncio.run_in_executor` to wrap the sync yfinance calls since the service is async FastAPI
- **Market holidays differ** between US and Sweden — maintain a Swedish market calendar (SE has different holidays than NYSE)
- **Backfill on startup:** fetch last 90 days of daily closes for standard assets at service startup so verification has data immediately without waiting
- **1-hour price cache** in memory — avoid redundant API calls when multiple predictions for the same asset land in the same hour
- **Minimum alpha=1, beta=1 always** — never let either go below 1 (prevents division by zero and extreme 0.0/1.0 credibility from small samples)
- scipy.stats.beta.interval is the correct function for the CI — not norm.interval

---

## Relevant Tasks
- [SRS-05 — Market Data Service](../../requirements/SRS-05-market-data.md) (price adapters; yfinance superseded by biquote — see POC-7)
- [SRS-06 — Verification Service](../../requirements/SRS-06-verification.md) (close-to-close scoring with deadband)
- [SRS-07 — Credibility Service](../../requirements/SRS-07-credibility.md) (Beta-Bernoulli edge weight updater)
