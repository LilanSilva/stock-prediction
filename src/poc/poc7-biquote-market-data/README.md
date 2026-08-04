# POC-7 biquote.io Market-Data Probe

Standard-library harness (no third-party deps) that validates whether **biquote.io** can replace the
Yahoo Finance chart source as the reference-price provider for the two canonical POC assets, without
touching any production code.

Motivation: the live Yahoo chart endpoint rate-limits this host's IP (`HTTP 429`), which has left 7
predictions stuck `PENDING` (no price could ever be fetched to score them). biquote is a free,
key-less alternative; this POC checks whether it meets every requirement the real Market Data
adapter depends on before we commit to a migration.

> **Correction (2026-08-04): the 429 diagnosis above was wrong.** Yahoo blocks non-browser
> User-Agents; it was not rate-limiting this host's IP. POC-6 sent
> `USER_AGENT = "feed-analyzer-poc6/1.0 (local research POC)"` (`src/poc/poc6/poc6.py:27`). Re-probed
> with a browser User-Agent: **40 concurrent requests all returned 200**, while 8 with curl's default
> agent all returned **429**. The migration to biquote remains sound on its own merits (documented,
> stable, no UA trickery), but biquote turned out to serve only a curated list of US mega-caps — every
> European listing returns 0 bars, including EU giants on US exchanges (`ASML`, `SAP`, `NVO`, `SHEL`).
> Yahoo is therefore back in use for non-US listings via `market_data.adapters.yahoo`, which sends a
> browser User-Agent. See `docs/reference/asset-registry.md` and ADR-007.

## Run

```bash
cd src/poc/poc7-biquote-market-data
python probe.py --output results/poc7-biquote.json
```

The probe reads only public, key-less endpoints, mutates nothing, and never touches the running
stack, the database, or the frozen `poc6-yahoo-reference-v1` policy. Exit code 0 = all gates PASS.

## Gates

| Gate | Requirement it protects | Result |
|------|-------------------------|--------|
| G1 | Both assets resolve to a biquote symbol returning daily data (GOLD→`XAUUSD`, BRENT_OIL→`UKOIL`) | PASS |
| G2 | Historical `from`/`to` date-range query returns the bracketing sessions (needed for baseline+settlement) | PASS |
| G3 | The current unsettled bar is flagged `isOpen=true` so only final closes are recorded | PASS |
| G4 | Bar-time convention is usable for session mapping (found: **UTC midnight**, not intraday like Yahoo) | PASS |
| G5 | Response shape understood: **no Yahoo-style `meta` block** — registry metadata validation must be replaced | PASS |
| G6 | No observable rate limit under a burst (the reason for leaving Yahoo) — 60/60 → `200` | PASS |

Full evidence in [results/poc7-biquote.json](results/poc7-biquote.json).

## Outcome

**Overall: PASS.** biquote covers both assets with real daily closes, supports historical
date-range queries, exposes an `isOpen` flag to exclude unsettled bars, and showed no throttling.

The migration is **not a blind find-and-replace**: biquote's JSON differs from Yahoo's in ways a new
adapter must handle (no `meta` block; UTC-midnight bar times vs Yahoo's intraday stamps + 17:00
America/New_York session logic; `isOpen` final-close semantics). It also changes economic identity
(`XAUUSD` spot gold vs Yahoo `GC=F` COMEX futures; `UKOIL` Brent vs `BZ=F`), which requires a **new
reference-price policy version** (`biquote-reference-v1`) rather than reusing the frozen Yahoo one.

Caveats recorded: no stated rate limit/SLA/terms (free, key-less — could change without notice);
smaller aggregated source (Yahoo bootstrap + MT5 feeds); daily series can have gaps (in the test
window BRENT returned fewer bars than GOLD).

See the decision doc: [backlog/POC/poc-7-biquote-price-source.md](../../../backlog/POC/poc-7-biquote-price-source.md).
