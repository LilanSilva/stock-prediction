# POC-7: biquote.io as Reference-Price Source (Yahoo replacement)

**Status:** COMPLETE — validated, awaiting go/no-go on migration
**Date:** 2026-07-30
**Outcome:** PASSED (all 6 gates) — biquote.io is a viable replacement for the Yahoo Finance chart
source, subject to a new reference-price policy version and a rewritten adapter.

> This POC does **not** change production. No adapter was removed, no registry entry was edited, and
> the frozen `poc6-yahoo-reference-v1` policy is untouched. The executable harness and its results
> live in [`src/poc/poc7-biquote-market-data/`](../../src/poc/poc7-biquote-market-data/).

---

## Why This Was Investigated

The live Yahoo Finance chart endpoint (`query1.finance.yahoo.com/v8/finance/chart`) rate-limits this
host's IP with `HTTP 429 Too Many Requests`. Consequence observed in the running stack:

- 8 predictions were made and reached Verification; all 8 `PriceRequested` messages were delivered.
- Only **1** price round-trip succeeded (during a brief unthrottled window); it scored 1 correct.
- The other **7** are stuck `PENDING` — every fetch attempt returns 429, so they dead-letter to
  `market-data.price-requests.dlq` and are re-driven every 15 minutes, never succeeding.

The 429 is environmental (a key-less public endpoint throttling by IP), not a code defect. biquote.io
is a free, no-registration alternative; this POC checks whether it fits before any migration.

---

## What Was Tested (live, against biquote.io)

A standard-library probe ran one gate per requirement the real Market Data adapter depends on:

| Gate | Requirement | Result | Evidence |
|------|-------------|--------|----------|
| G1 | Both assets return daily data | **PASS** | `XAUUSD` close ≈ 4103, `UKOIL` close ≈ 86.9 |
| G2 | Historical `from`/`to` range query | **PASS** | 2026-07-24…28 window returns bars inside the window |
| G3 | Unsettled bar flagged `isOpen=true` | **PASS** | newest bar `isOpen=true`; ≥1 closed bar per series |
| G4 | Bar-time convention for sessions | **PASS** | bars stamped **UTC midnight** (`...T00:00:00Z`) |
| G5 | Response shape (no `meta` block) | **PASS** | keys = `symbol, interval, bars`; no Yahoo `meta` |
| G6 | No rate limit under burst | **PASS** | 60 concurrent requests → 60× `200`, zero 429 |

Full machine-readable output: `src/poc/poc7-biquote-market-data/results/poc7-biquote.json`.

---

## Decision: candidate mapping

| Canonical asset | Yahoo (current) | biquote (proposed) | biquote description |
|-----------------|-----------------|--------------------|---------------------|
| `GOLD` | `GC=F` (COMEX gold futures) | `XAUUSD` | Gold / US Dollar (spot) |
| `BRENT_OIL` | `BZ=F` (NYMEX Brent futures) | `UKOIL` | "Crude Oil Brent" (NYMEX) |

- **API base:** `https://biquote.io/api`
- **Endpoint:** `GET /api/{symbol}/ohlc?interval=1d&from={ISO8601}&to={ISO8601}`
- **Auth:** none (key-less; `X-Api-Key` only for admin endpoints)
- **Close field:** `bars[].close`; bar time `bars[].openTime`; final-close guard `bars[].isOpen == false`

---

## What a Real Migration Must Handle (NOT a find-and-replace)

biquote's JSON differs from Yahoo's in four ways the current adapter actively relies on:

1. **No `meta` block.** The Yahoo adapter validates `currency` / `exchangeName` /
   `exchangeTimezoneName` against the registry. biquote returns none of these — that validation must
   be dropped or replaced (e.g. trust the symbol mapping + a positive-close sanity check).
2. **Bar timestamps are UTC midnight**, not Yahoo's intraday bar times mapped to a 17:00
   America/New_York session. The session-mapping logic (`shared.calendar`) must treat a biquote
   `openTime` date as the session date directly, or it will be **off by one**.
3. **`isOpen=true` on the current bar.** biquote returns the still-forming day as a last-tick close.
   The adapter must **exclude `isOpen=true` bars** so only settled closes are recorded.
4. **Economic identity changes.** `XAUUSD` is spot gold (not COMEX futures); `UKOIL` is a
   broker-fed Brent series (not the `BZ=F` continuous). Directionally comparable, but a different
   instrument — this is why a new policy version is required, not a silent swap.

---

## Frozen-Policy Impact

The reference-price policy `poc6-yahoo-reference-v1` is stamped on every `CloseObservation` and is
**validated by Verification** (asset, sessions, `price_kind`, `is_adjusted`, `registry_version`).
Switching provider therefore requires a **new version** — proposed `biquote-reference-v1` — with
honest metadata (real provider = biquote, symbols `XAUUSD`/`UKOIL`, spot/Brent identity).

**Consequence for the 7 stuck predictions:** they were created and stamped under
`poc6-yahoo-reference-v1`. Under a new version their evaluations can never be fulfilled (the version
would not match), so the agreed plan is to **purge the 7 stale evaluations** (and their dead-lettered
price requests) when the new policy goes live. Future predictions score correctly via biquote.

---

## Caveats / Risks (recorded honestly)

- **No stated rate limit, SLA, or terms of service.** Free and key-less — generous today (G6), but
  could add throttling or change without notice, with no account or support channel for recourse.
- **Smaller aggregated source.** Data is "Yahoo bootstrap + MT5 broker feeds," not an authoritative
  exchange feed. Fine for POC-grade directional scoring; not a hardened production dependency.
- **Daily series can be sparse.** In the 2026-07-24…28 test window `UKOIL` returned fewer bars than
  `XAUUSD`; the adapter's existing "session not yet available → stay pending" path already covers
  this, but it is a data-quality note.

---

## Recommendation

biquote.io **passes** as a Yahoo replacement for the POC and directly resolves the 429 blocker. If
approved, the implementation task is:

1. Add `biquote-reference-v1` to the shared asset registry (`XAUUSD`/`UKOIL`, honest metadata).
2. Replace `YahooChartAdapter` with a `BiquoteAdapter` (no-`meta`, UTC-midnight sessions,
   `isOpen=false` filter); remove the Yahoo adapter + its tests.
3. Update `market_data` config/DI and docker-compose env (base URL, symbols).
4. Purge the 7 stale `poc6-yahoo` evaluations + their DLQ requests.
5. ~~Update E05 backlog docs~~ — E05 backlog folder removed; see [SRS-05](../../requirements/SRS-05-market-data.md) for as-built biquote adapter documentation.

**Not started** — awaiting explicit go/no-go based on this POC.

---

## Relevant Tasks
- [SRS-05 — Market Data Service](../../requirements/SRS-05-market-data.md)
- Supersedes the provider-selection portion of [POC-4](poc-4-market-data-verification.md) (yfinance)
  and the Yahoo confirmation in [POC-6](poc-6-end-to-end-prediction-validation.md) if adopted.
