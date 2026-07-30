# POC-8 FreeNewsApi.io Ingestion-Source Probe

Standard-library harness (no third-party deps) that evaluates **FreeNewsApi.io**
(`https://api.freenewsapi.io/v1`) as a keyed news source to sit alongside/replace **GDELT**, which
currently returns 0 records under `HTTP 429` IP rate-limiting.

The API key is read from `FREENEWSAPI_KEY` and is **never** written to disk, logged, or embedded in
the results file — results record only observed behaviour (status codes, rate-limit headers, article
counts).

## Run

```bash
cd src/poc/poc8-freenewsapi
FREENEWSAPI_KEY=<your key> python probe.py --output results/poc8-freenewsapi.json
```

Frugal with quota (~24 calls against a 5,000/day budget). Exit code 0 = all gates PASS.

## Gates & Results (2026-07-30)

| Gate | Requirement | Result |
|------|-------------|--------|
| G1 | Authenticated fetch (`x-api-key`) → 200 and rate-limit headers exposed | PASS |
| G2 | Keyword search for oil/gold/OPEC/sanctions/inflation returns articles | PASS (10 each) |
| G3 | Response maps to `ArticleIngested` (title, `original_url`, `published_at`, language, **body**) | PASS (body ~700–3000 chars) |
| G4 | **Rate limit:** paced ≤2 req/sec all succeed; daily budget read from headers | PASS (5/5 → 200) |
| G5 | **Rate limit:** deliberate no-delay burst degrades gracefully (only 200/429) | PASS (10/10 → 200) |

## Rate-limit finding (the headline result)

The service exposes its limits in response headers, so the budget is **observable, not guessed**:

```
X-RateLimit-Limit-Day: 5000
X-RateLimit-Remaining-Day: 4992
X-RateLimit-Reset-Day: 2026-07-31T22:36:04.000Z
```

- **Daily budget: 5,000 requests** (per key, resets daily). Ingestion polls hourly and needs ~1
  request per keyword per poll (~120/day for 5 keywords) — well under 4% of the budget.
- **Burst:** 10 back-to-back calls all returned `200` — the documented "2 req/sec" cap was not even
  tripped at this volume, and there were **no `429`s** (contrast: GDELT 429s on the first call).

## Outcome

**Overall: PASS.** FreeNewsApi.io authenticates cleanly via an `x-api-key` header, returns relevant
articles for every ingestion keyword, provides **full article bodies** via `/details` (GDELT
provides none), and exposes a generous, header-visible daily budget that our hourly polling will
never approach. It directly solves the GDELT-429 problem.

Caveats: requires a key (free, no billing, but a secret to manage); two-step fetch (list `/news`
then `/details` per article for the body) costs 1 + N requests per poll — still trivial against
5,000/day; free service with no SLA (POC-grade, like biquote).

See the decision doc: [backlog/POC/poc-8-freenewsapi-source.md](../../../backlog/POC/poc-8-freenewsapi-source.md).
