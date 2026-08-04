# Canonical Asset Registry

Business messages use `asset_id`. Adapters translate that ID to a versioned provider reference series. Provider symbols must never be used as cross-service business identifiers.

## Source of truth: `assets.json`

The registry is **data, not code**. It is loaded from JSON so a company or a whole market can be added
without a code change, a redeploy of the shared library, or a database migration (asset ids are
already `TEXT` in Postgres and `Asset.id` in Neo4j).

| | |
|---|---|
| Packaged default | `src/shared/shared/reference/assets.json` (ships inside the wheel) |
| Deployed override | `ASSET_REGISTRY_PATH`, mounted read-only at `/config/assets.json` from `infra/assets/` |
| Loader | `shared.reference.loader` |
| Accessors | `shared.reference.resolve` / `group_of` / `members_of` / `provider_of` / `supported_assets` |

Editing the mounted file plus a service restart is the whole workflow. The registry loads once at
startup by design: swapping reference data mid-flight would let one message validate against a
different asset set than the one that produced it.

Current registry version: **`multi-market-v2`** — 18 groups, 34 assets, 4 currencies, 6 markets.

### `AssetId` is a validated string, not a closed enum

`AssetId` (`shared.schemas.asset_id`) subclasses `str` and validates against the loaded registry. It
keeps the former `StrEnum` surface — `AssetId.GOLD`, `AssetId("GOLD")`, `.value`, iteration,
membership, and interned instances so `is` comparisons hold — so existing call sites are unchanged.

An unknown id is rejected at construction and at every message boundary, so an unrecognised asset is
never silently accepted. The trade-off accepted: mypy can no longer prove exhaustive coverage over
assets, because membership is data-driven.

### File shape

```json
{
  "registry_version": "multi-market-v2",
  "rollover_policy": "PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1",
  "groups": [
    {
      "group_id": "WEAPON_INDUSTRY",
      "display_name": "Weapon & defence industry",
      "industry_keywords": ["defense", "defence", "missile"],
      "assets": [
        {
          "asset_id": "SAAB_B_STO",
          "name": "Saab AB",
          "code": "STO:SAAB-B",
          "provider": "yahoo",
          "provider_symbol": "SAAB-B.ST",
          "economic_identity": "Saab AB ordinary shares (STO)",
          "expected_exchange": "STO",
          "currency": "SEK",
          "timezone": "Europe/Stockholm",
          "session_complete_at": "18:00",
          "price_kind": "PROVIDER_DAILY_CLOSE",
          "is_adjusted": false,
          "fallback": null,
          "keywords": ["saab", "saab-b"]
        }
      ]
    }
  ]
}
```

Field rules:

| Field | Rule |
|---|---|
| `asset_id` | Canonical id crossing service boundaries. UPPER_SNAKE_CASE, **no colon** — it is stored as `TEXT` in Postgres, as `Asset.id` in Neo4j, and embedded in the prediction idempotency key. |
| `code` | Human-readable `EXCHANGE:TICKER`. **Documentation only** — never sent to a provider (biquote rejects the format outright). |
| `provider` | Selects the Market Data adapter. Must be a known provider. |
| `provider_symbol` | The exact string the provider expects. Differs per vendor for the same instrument: `STO:SAAB-B` documents, Yahoo wants `SAAB-B.ST`, Finnhub wants `SAAB B.ST`. |
| `keywords` | Company-scope inference keywords (see below). Must be unique across all assets. |
| `industry_keywords` | Group-scope keywords; a match fans out to every member. |
| `currency` / `timezone` / `session_complete_at` | Required for scoring and the per-market session calendar. |

The loader **refuses to boot** on a malformed file rather than falling back silently: duplicate
`asset_id`, a colon in an id, a keyword claimed by two assets, an unknown provider, an invalid
session time, a `group_id`/`asset_id` collision, an empty group, or a fallback naming an undeclared
asset. A silent fallback would be worse — you would believe an edit took effect while the service ran
on stale data.

### Validation

```
python scripts/validate-assets.py                      # structure + live provider probe
python scripts/validate-assets.py --offline            # structure only (CI)
python scripts/generate-asset-seed.py --check          # Neo4j seed matches the registry
```

Structural validation alone cannot catch a typo'd ticker: the registry would load, the asset would
predict, and scoring would stall silently on `PriceNotYetAvailable`. The probe hits every
`provider_symbol` and cross-checks the provider's reported currency and timezone against the file.

## Groups and news scope

Every asset belongs to exactly one group — commodities included (`GOLD` is in `PRECIOUS_METALS`), so
there is no special case downstream. A group is a **fan-out target**, never itself tradeable: one
asset must map to exactly one price series or scoring breaks.

Cleansing resolves which assets an article affects by scope, most specific first:

| Scope | Trigger | Result |
|---|---|---|
| `COMPANY` | a company's own keyword | that asset alone |
| `INDUSTRY` | a group keyword | **every member of the group**, across markets |
| `EVENT_TYPE` | neither | the event type's graph assets (`EVENT_TYPE_ASSETS` + `EVENT_TYPE_GROUPS`) |
| `NONE` | nothing matches | no assets; no prediction |

```
"Tesla acquired by rival"     -> COMPANY   -> TSLA_NASDAQ
"Missile strikes hit airbase" -> INDUSTRY  -> LMT_NYSE, SAAB_B_STO, AM_EPA
```

Causal knowledge is seeded at **group** level (`infra/neo4j/init/06-seed-group-edges.cypher`) and
inherited by every member, so a listing predicts from day one without company-specific evidence. An
asset's own edge always overrides its group's. This is what makes adding a market useful rather than
merely possible: without a group prior a newly added listing resolves, prices, and then produces no
prediction, because the decision policy needs at least one firing edge.

A company keyword always beats an industry keyword in the same text, so a Saab-specific story does
not move the whole defence sector. Single-token keywords match whole-word with English/Swedish plural
tolerance (`missiles` fires `missile`); multi-word phrases match as substrings.

**Open policy knob:** an industry event currently fans out to *all* members, so one headline over a
4-member group produces 4 predictions whose outcomes are correlated but scored as independent. This
inflates apparent confidence and is not yet bounded.

## Provider routing

No single vendor covers every market, so `provider` is per asset and Market Data dispatches through
`AdapterRouter`.

| Provider | Serves | Notes |
|---|---|---|
| `biquote.io` | a curated list of US mega-caps | 12 assets |
| `yahoo` | Stockholm, Copenhagen, Amsterdam, Paris, Xetra, **and US names biquote lacks** | 22 assets |

**biquote is limited by its symbol list, not by exchange.** Probed 2026-08-03: every European listing
returns 0 bars, *including* EU giants that trade on US exchanges (`ASML`, `SAP`, `NVO`, `SHEL`), and
8 requested US names (`BNTX`, `UAL`, `FANG`, `SPG`, `VLO`, `ZM`, `MRNA`, `PTON`) also return 0. Those
8 route to Yahoo. `EXCHANGE:TICKER` prefixes are rejected outright.

**Yahoo requires a browser User-Agent.** The `HTTP 429`s that retired Yahoo in POC-7 were caused by
its custom agent (`feed-analyzer-poc6/1.0`), **not** by IP rate limiting: 40 concurrent requests with
a browser agent all return 200, while 8 with curl's default agent all return 429. The adapter sends a
browser UA and a test asserts the string does not identify this service. Accept the caveats: the
endpoint is undocumented and UA-sniffing may change without notice, which is why US assets stay on
biquote — Yahoo flakiness cannot regress existing scoring, and switching to a paid vendor is one
field in the JSON.

**Finnhub** was evaluated as the paid alternative (2026-08-03). Its `/search` does know European
listings, but every historical-price endpoint returns `403` on the free tier — including
`/stock/candle` for US symbols, at every resolution. The `60/min` rate limit is not the constraint;
endpoint entitlement is. `/quote` cannot substitute: it returns a live snapshot with no date
parameter, and the adapter contract needs `get_close(asset_id, session)` for specific past sessions.
Viable on a paid plan; confirm `.ST` coverage before subscribing.

## Session calendars

`shared.calendar` resolves any IANA timezone through stdlib `zoneinfo`. Each asset's `timezone` and
`session_complete_at` drive baseline/settlement resolution, session completion, and Prediction's
market-open stance.

| Market | Local close | UTC (July) |
|---|---|---|
| America/New_York | 17:00 | 21:00 |
| Europe/Stockholm | 18:00 | 16:00 |

DST comes from the tz database, not hardcoded rules: on 2026-03-10 New York is already `UTC-4` while
Stockholm is still `UTC+1`, so one hand-rolled rule cannot serve both markets.

**Holidays are deliberately not modelled.** A session is any weekday; a local holiday surfaces as a
missing provider bar and the request stays `PENDING` and retries — identical to existing US-holiday
behaviour. The cost: on a market holiday a request waits out its retry budget rather than skipping to
the next real session.

## Required reference-series fields

Before an asset may be scored, its registry entry must declare:

- provider and provider symbol;
- instrument type and currency;
- intended economic asset and provider exchange metadata;
- session-date mapping, timezone, and session completion clock;
- `price_kind`, initially either `PROVIDER_DAILY_CLOSE` or `OFFICIAL_SETTLEMENT`;
- whether prices are raw or adjusted;
- continuous-futures rollover policy;
- fallback mapping or explicit `null`.

Provider daily data is `PROVIDER_DAILY_CLOSE`. It must not be described as an official exchange
settlement. Raw (unadjusted) closes only: Yahoo's `adjclose` is back-adjusted for splits and
dividends and would silently change historical values between fetches, breaking immutable
observations.

## Registry rules

- Registry changes are reviewed as contract changes.
- Canonical market identity and provider bar metadata are recorded separately when they differ.
- A provider timestamp is not assumed to be an official settlement time.
- Fallbacks must represent the same economic instrument, currency, session semantics, and adjustment policy.
- An unavailable fallback is `null`; the system must not silently substitute a different listing, ETF, or contract.
- Rollover exclusions are declared before outcomes are evaluated.
- API filters accept canonical IDs. UI labels come from the registry.
- Regenerate the Neo4j seed after any registry edit (`python scripts/generate-asset-seed.py`).

## History

- **`multi-market-v2`** (2026-08-04) — 34 assets across US/SE/DK/NL/FR/DE in USD, SEK, EUR, DKK. JSON-driven registry, industry groups, per-asset provider routing, per-market calendars.
- **`multi-market-v1`** — first JSON registry; the 15 sector-bellwether ids (`PHARMA`, `SOFTWARE`, …) were replaced by per-company ids. `GOLD` and `BRENT_OIL` were retained: they carry the seeded Neo4j `CAUSES` edges and all existing prediction/score history.
- **`biquote-reference-v1`** (POC-7) — hardcoded Python registry; `XAUUSD`/`UKOIL` plus 15 US sector bellwethers, biquote only.
- **`poc6-yahoo-reference-v1`** — retired; `GC=F`/`BZ=F` via the Yahoo chart endpoint. Retired for `HTTP 429`s later shown to be a User-Agent problem rather than IP rate limiting.

Deferred: `USD_INDEX`, `OMXS30`, `SP500` and other index instruments, pending validated provider
mappings, currency, calendars, and fallback quality.
