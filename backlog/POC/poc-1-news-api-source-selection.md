# POC-1: News API Source Selection

**Status:** COMPLETE
**Date:** 2026-06-25
**Outcome:** PASSED — 4 free, real-time sources identified and live-tested

> **POC-6 revalidation (2026-07-13):** All four Swedish RSS endpoints remained live. GDELT returned HTTP 429 and later transport failures beyond a simple five-second cooldown. Retain GDELT only as an optional independently failing source with `Retry-After`, exponential backoff/jitter, caching, and circuit breaking; do not implement the fixed-sleep recommendation below as authoritative behavior.

---

## What Was Investigated

Evaluated 13+ free news APIs and RSS sources for suitability in an hourly-polling system.
The single most important criterion: **does the free tier give genuinely fresh news, or is it delayed?**

---

## Key Finding: The Delay Trap

Several widely-known "free" APIs deliberately delay their free tier — making hourly polling pointless:

| Source | Free-tier delay | Decision |
|---|---|---|
| **NewsAPI.org** | **24 hours** (officially stated) | ❌ DISQUALIFIED |
| **GNews.io** | **12 hours** | ❌ DISQUALIFIED |
| **NewsData.io** | **12 hours** | ❌ DISQUALIFIED |
| **Mediastack** | "Delayed" (unquantified) | ❌ DISQUALIFIED |
| **Finnhub** | Fresh, but US-only | ❌ DISQUALIFIED (no Swedish) |
| **Polygon.io** | Updated hourly, US-only | ❌ DISQUALIFIED (no Swedish) |
| **Alpha Vantage** | Fresh, but 25 req/day only | ❌ DISQUALIFIED (too few) |
| **Reuters RSS** | Shut down June 2020 | ❌ DISQUALIFIED (dead) |

---

## Live Test Results (run 2026-06-25 ~12:00 UTC)

### Dagens Industri RSS (`https://www.di.se/rss`)
```
NOW UTC: Thu, 25 Jun 2026 12:00
Items returned: 20
Top article: [Thu, 25 Jun 2026 11:54:27 GMT] Lars Wingefors gör storköp i Storytel
```
**Result: Top article was 6 minutes old. CONFIRMED real-time.**
Finance-specific content (Storytel, Skanska, H&M earnings). No API key. No rate limit.

### Dagens Nyheter RSS (`https://www.dn.se/rss/`)
```
Items returned: 119
Top article: [Thu, 25 Jun 2026 13:29:27 +0200] Libyens stridande lovar allmänna val
```
**Result: Minutes old. CONFIRMED real-time.**

### SvD Näringsliv (`https://www.svd.se/feed/articles.rss`)
```
Items returned: 30
Top article: [Thu, 25 Jun 2026 13:42:58 +0200] Man död i drunkningsolycka i Strömstad
```
**Result: Minutes old. CONFIRMED real-time.**

### Aftonbladet (`https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/`)
```
Items returned: 39
Top article: [Thu, 25 Jun 2026 11:44:38 GMT] Man död i drunkningsolycka i Strömstad
```
**Result: ~15 minutes old. CONFIRMED real-time.**

### GDELT DOC 2.0 API
Live test was rate-limited during POC (the research agents hit it repeatedly in the same hour, triggering the 1-req/5s throttle). **Rate limit confirmed real** — it enforces strictly.
Freshness confirmed by documentation: **15-minute update cycle**, no API key required.
Swedish sources confirmed indexed: `sourcecountry:SW` parameter verified in docs.

### Note on encoding
DI RSS returned garbled characters in the live test (`g\xf6r stork\xf6p` instead of `gör storköp`). This is a Latin-1/UTF-8 mismatch — a trivial one-line fix. **Not a data quality issue, just an encoding normalization step.**

---

## Final Recommended Sources

| # | Source | URL | Type | Coverage | Key? | Refresh |
|---|---|---|---|---|---|---|
| 1 | **Dagens Industri** | `https://www.di.se/rss` | RSS | Sweden + Finance | No | Minutes |
| 2 | **Dagens Nyheter** | `https://www.dn.se/rss/` | RSS | Sweden general | No | Minutes |
| 3 | **SvD Näringsliv** | `https://www.svd.se/feed/articles.rss` | RSS | Sweden business | No | Minutes |
| 4 | **Aftonbladet** | `https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/` | RSS | Sweden breaking | No | Minutes |
| 5 | **GDELT DOC 2.0** | `https://api.gdeltproject.org/api/v2/doc/doc` | REST | Global | No | 15 min |

---

## Implications for Implementation

- **Hourly polling is justified** for all 5 sources — all refresh within minutes.
- **No API keys needed** for any of these — zero-cost, no registration.
- **GDELT rate limit (1 req/5s) must be enforced** — use `asyncio.sleep(5)` between requests. Violating it returns 429 and a throttle window that persists beyond 5s if hammered.
- **Swedish RSS encoding**: always handle Latin-1/UTF-8 ambiguity. Use `feedparser` which handles most cases; fall back to `title.encode('latin-1').decode('utf-8', errors='replace')` when replacement chars appear.
- **Marketaux** (financial news with sentiment scores, instant, 100 req/day) is a viable add-on for financial-specific coverage. Swedish coverage unconfirmed — test with `countries=se` before relying on it.

---

## Relevant Tasks
- [SRS-02 — Ingestion Service](../../requirements/SRS-02-ingestion.md) (news source adapters: GDELT, Swedish RSS, FreeNewsApi)
