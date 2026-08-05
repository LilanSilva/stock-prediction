# POC-8: FreeNewsApi.io as Ingestion News Source (GDELT alternative)

**Status:** COMPLETE — validated
**Date:** 2026-07-30
**Outcome:** PASSED (all 5 gates) — FreeNewsApi.io is a viable keyed news source that resolves the
GDELT rate-limit failure, subject to managing a free API key.

> POC only. No ingestion code was changed. The executable harness and results live in
> [`src/poc/poc8-freenewsapi/`](../../src/poc/poc8-freenewsapi/).

---

## Why This Was Investigated

The Ingestion Service's GDELT source returns **0 records**: the GDELT API responds `HTTP 429 Too
Many Requests` (IP rate-limited), retries once, still 429, then skips GDELT for the poll
(`per_source_counts: {..., gdelt: 0}`). The four RSS feeds still work (~188 articles/poll), so
ingestion is not down — but GDELT specifically contributes nothing. This POC evaluates
FreeNewsApi.io as a replacement, with the primary question being **its rate limits**.

Prior alternatives ruled out this session: **TheNewsAPI** (key required; free tier only 100 req/day
× 3 articles — too small). FreeNewsApi.io was chosen for evaluation next.

---

## What Was Tested (live, with a free key)

| Gate | Requirement | Result | Evidence |
|------|-------------|--------|----------|
| G1 | Authenticated fetch + rate headers | **PASS** | `x-api-key` header → 200; `X-RateLimit-*-Day` headers present |
| G2 | Keyword search (oil/gold/OPEC/sanctions/inflation) | **PASS** | 10 articles returned for every keyword |
| G3 | Maps to `ArticleIngested` contract | **PASS** | `/details` returns `title`, `original_url`, `published_at`, `languages`, **`body`** (~700–3000 chars) |
| G4 | Paced ≤2 req/sec succeed; budget from headers | **PASS** | 5/5 → 200; `Limit-Day=5000`, `Remaining-Day` decremented live |
| G5 | Burst degrades gracefully (only 200/429) | **PASS** | 10 back-to-back calls all 200; no 429 |

Machine-readable output: `src/poc/poc8-freenewsapi/results/poc8-freenewsapi.json`.

---

## Rate Limit — the headline answer

FreeNewsApi.io **exposes its limits in response headers**, so the budget is observable, not guessed:

```
X-RateLimit-Limit-Day: 5000
X-RateLimit-Remaining-Day: 4992
X-RateLimit-Reset-Day: 2026-07-31T22:36:04.000Z
```

- **Daily budget: 5,000 requests/key** (resets daily). A documented **2 req/sec** throughput cap
  also exists, but a 10-call no-delay burst did not trip it (all 200).
- **Our usage is negligible:** hourly polling × 5 keywords ≈ 120 requests/day for listings, plus
  one `/details` call per new article for the body. Even fetching bodies for, say, 100 articles/day
  is ~220 requests/day — **under 5%** of the 5,000 budget.
- **Contrast with GDELT:** GDELT 429s on the very first call with no documented budget; FreeNewsApi
  gives a large, header-visible budget and did not throttle at all in testing.

---

## Decision: candidate integration shape

- **Base URL:** `https://api.freenewsapi.io/v1`
- **Auth:** `x-api-key: <key>` header (free signup, no billing). Key is a secret — inject via env
  (`FREENEWSAPI_KEY` / a service setting), never commit.
- **Endpoints:** `/news` (list: `uuid`, `title`, `published_at`, `publisher`) then `/details?uuid=`
  (adds `body`, `original_url`, `languages`, `topics`, `countries`, `authors`).
- **Search:** `in_title` / `in_body` / `in_subtitle` (the old `q` param is **removed** — returns
  `400`). Filter `language`, `country`, `published_after/before`, paginate via `next_cursor`.
- **Bodies:** unlike GDELT (headline only), FreeNewsApi returns full article `body` — a genuine
  upgrade for the Cleansing/Prediction pipeline.

---

## Caveats / Risks (recorded honestly)

- **Requires a key** (free, no billing, but a secret to manage — unlike GDELT/biquote which are
  key-less). Rotate the key used for this POC since it was shared in chat.
- **Two-step fetch** (list then per-article `/details`) means 1 + N requests per poll to get bodies.
  Trivial against 5,000/day, but the adapter must budget for it.
- **Free service, no SLA** — POC-grade, same caveat as biquote (POC-7). Could change/limit without
  notice; the header-visible budget at least makes throttling detectable.

---

## Recommendation

FreeNewsApi.io **passes** and is a clear improvement over the GDELT source: relevant results for
every keyword, full article bodies, and a generous header-visible rate budget our polling will never
approach. If approved, the implementation task would:

1. Add a `FreeNewsApiSource` to the Ingestion Service (`x-api-key` header, `in_title` search,
   `/news` → `/details` for bodies) mapping to `ArticleIngested`.
2. Read the key from a service setting (`FREENEWSAPI_KEY`) + docker-compose env; never commit it.
3. Replace or supplement the GDELT source (decision: replace GDELT, keep the 4 RSS feeds).
4. Respect the 2 req/sec cap (small sleep between calls) and log `X-RateLimit-Remaining-Day`.

**Not started** — awaiting go/no-go on the implementation based on this POC.

---

## Relevant Tasks
- [SRS-02 — Ingestion Service](../../requirements/SRS-02-ingestion.md) (news source adapters)
- Related provider-migration precedent: [POC-7 biquote](poc-7-biquote-price-source.md).
