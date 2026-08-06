# REF-02: Canonical Asset Registry

## 1. Document control

| | |
|---|---|
| Document ID | `REF-02` |
| Type | Reference data |
| Registry version | `multi-market-v2` |
| Status | `Implemented` |
| Version | `1.0.0` |
| Executable source | [src/shared/shared/reference/assets.json](../src/shared/shared/reference/assets.json), loader [shared/reference/loader.py](../src/shared/shared/reference/loader.py) |
| Deployed override | [infra/assets/assets.json](../infra/assets/assets.json) via `ASSET_REGISTRY_PATH` |
| Consumed by | [SRS-01 §9.3](SRS-01-shared-foundation.md#93-asset-registry-file), [SRS-03](SRS-03-cleansing.md), [SRS-04](SRS-04-prediction.md), [SRS-05](SRS-05-market-data.md), [SRS-06](SRS-06-verification.md) |
| Last verified against `assets.json` | `2026-08-06` |

This document is **reference data, not a requirement specification.** It contains no `shall`
statements. The requirements that constrain it are
[`SYS-9` … `SYS-14`](SyRS-system.md#52-identity-and-reference-data).

`assets.json` is the executable source of truth. Every count and field rule below must match it.

**Current registry:** 16 groups, 37 assets (34 primary + 3 fallback entries), 4 currencies
(USD, SEK, EUR, DKK), 6 market timezones, 9 exchanges.

## 2. The registry is data, not code

Business messages carry `asset_id`. Adapters translate that ID to a versioned provider reference
series. **A provider symbol is never a cross-service business identifier.**

The registry loads from JSON so a company — or a whole market — can be added without a code change, a
shared-library redeploy, or a database migration. Asset IDs are already `TEXT` in Postgres and
`Asset.id` in Neo4j, so there is nothing to migrate.

| | |
|---|---|
| Packaged default | `src/shared/shared/reference/assets.json` (ships inside the wheel) |
| Deployed override | `ASSET_REGISTRY_PATH`, mounted read-only at `/config/assets.json` from `infra/assets/` |
| Loader | `shared.reference.loader` |
| Accessors | `shared.reference.resolve` / `group_of` / `members_of` / `provider_of` / `supported_assets` |

Editing the mounted file plus a service restart is the whole workflow.

**The registry loads once at process start, by design** (`SYS-13`). Swapping reference data mid-flight
would let one message validate against a different asset set than the one that produced it.

### 2.1 `AssetId` is a validated string, not a closed enum

`AssetId` ([shared/schemas/asset_id.py](../src/shared/shared/schemas/asset_id.py)) subclasses `str`
and validates against the loaded registry. It keeps the former `StrEnum` surface — `AssetId.GOLD`,
`AssetId("GOLD")`, `.value`, iteration, membership, and interned instances so `is` comparisons hold —
so existing call sites were unchanged by the migration away from the enum.

An unknown ID is rejected at construction **and at every message boundary** (`SYS-10`), so an
unrecognised asset is never silently accepted.

**Accepted trade-off:** `mypy` can no longer prove exhaustive coverage over assets, because membership
is data-driven rather than statically declared. Recorded in
[SyRS §13.3](SyRS-system.md#133-known-limitations).

## 3. File shape

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

### 3.1 Field rules

| Field | Rule |
|---|---|
| `asset_id` | Canonical ID crossing service boundaries. `UPPER_SNAKE_CASE`, **no colon** — it is stored as `TEXT` in Postgres, as `Asset.id` in Neo4j, and embedded in the prediction idempotency key. |
| `code` | Human-readable `EXCHANGE:TICKER`. **Documentation only** — never sent to a provider (biquote rejects the format outright). |
| `provider` | Selects the Market Data adapter. Must be a known provider. |
| `provider_symbol` | The exact string the provider expects. Differs per vendor for the same instrument: `STO:SAAB-B` documents, Yahoo wants `SAAB-B.ST`, Finnhub wants `SAAB B.ST`. |
| `keywords` | Company-scope inference keywords (section 4). Must be unique across all assets. |
| `industry_keywords` | Group-scope keywords; a match fans out to every member. |
| `currency` / `timezone` / `session_complete_at` | Required for scoring and the per-market session calendar. |
| `price_kind` | `PROVIDER_DAILY_CLOSE` or `OFFICIAL_SETTLEMENT`. |
| `is_adjusted` | Raw closes only — see section 7. |
| `fallback` | An economically equivalent asset ID, or explicit `null`. |

### 3.2 The loader refuses to boot on a malformed file

Rejected outright: a duplicate `asset_id`, a colon in an ID, a keyword claimed by two assets, an
unknown provider, an invalid session time, a `group_id`/`asset_id` collision, an empty group, or a
`fallback` naming an undeclared asset.

This is deliberate (`SYS-14`). A silent fallback to stale or partial data would be worse than failing
loudly — you would believe an edit took effect while the service ran on the old asset set.

### 3.3 Validation

```bash
python scripts/validate-assets.py                      # structure + live provider probe
python scripts/validate-assets.py --offline            # structure only (CI)
python scripts/generate-asset-seed.py --check          # Neo4j seed matches the registry
```

**Structural validation alone cannot catch a typo'd ticker:** the registry would load, the asset would
predict, and scoring would then stall silently on `PriceNotYetAvailable`. The live probe hits every
`provider_symbol` and cross-checks the provider's reported currency and timezone against the file.

## 4. Groups and news scope

Every asset belongs to **exactly one** group — commodities included (`GOLD` is in `PRECIOUS_METALS`),
so there is no special case downstream.

A group is a **fan-out target, never itself tradeable**: one asset must map to exactly one price
series or scoring breaks. This is why multi-ticker assets were rejected — `LMT` (USD), `SAAB-B` (SEK)
and `AM` (EUR) are three companies, three prices, three currencies, and therefore three assets.

The 16 groups: `WEAPON_INDUSTRY`, `AEROSPACE_AVIATION`, `PHARMA_INDUSTRY`,
`SEMICONDUCTOR_INDUSTRY`, `SOFTWARE_INDUSTRY`, `INTERNET_PLATFORMS`, `CONSUMER_ELECTRONICS`,
`AUTOMOTIVE_INDUSTRY`, `OIL_GAS`, `PRECIOUS_METALS`, `BANKING_FINANCE`,
`INDUSTRIAL_MANUFACTURING`, `TELECOM`, `FOOD_INGREDIENTS`, `REAL_ESTATE`, `CONSUMER_FITNESS`.

Cleansing resolves which assets an article affects by scope, **most specific first**:

| Scope | Trigger | Result |
|---|---|---|
| `COMPANY` | a company's own keyword | that asset alone |
| `INDUSTRY` | a group keyword | **every member of the group**, across markets |
| `EVENT_TYPE` | neither | the event type's graph assets (`EVENT_TYPE_ASSETS` + `EVENT_TYPE_GROUPS`) |
| `NONE` | nothing matches | no assets; no prediction |

```text
"Tesla acquired by rival"     -> COMPANY   -> TSLA_NASDAQ
"Missile strikes hit airbase" -> INDUSTRY  -> LMT_NYSE, SAAB_B_STO, AM_EPA
```

A company keyword always beats an industry keyword in the same text, so a Saab-specific story does not
move the whole defence sector. Single-token keywords match whole-word with English/Swedish plural
tolerance (`missiles` fires `missile`); multi-word phrases match as substrings. The algorithm is
specified in [SRS-03 §7.5](SRS-03-cleansing.md#7-how-it-works).

### 4.1 Edge inheritance

Causal knowledge is seeded at **group** level
([06-seed-group-edges.cypher](../infra/neo4j/init/06-seed-group-edges.cypher)) and inherited by every
member, so a listing predicts from day one without company-specific evidence. An asset's own edge
always **overrides** its group's rather than adding to it.

This is what makes adding a market useful rather than merely possible: without a group prior a newly
added listing resolves, prices, and then produces **no prediction**, because the decision policy needs
at least one firing edge. Inheritance without a seeded prior is inert — the two belong together.

Group priors sit deliberately **below** the commodity edges: a macro factor moves a whole industry less
reliably than it moves gold, and these carry no evidence yet, so starting modest lets real outcomes
pull them up rather than having to walk them down. `CORPORATE_EARNINGS` carries a prior on every
group, because company-specific news is the case this feature exists for and that factor previously
had no edge at all.

Because an inherited `edge_id` names the **group** edge, Credibility updates the industry prior that
actually fired instead of inventing a per-asset edge that was never seeded. Edge key forms are in
[SyRS §9.2](SyRS-system.md#92-neo4j-graph-model).

**Open policy knob:** an industry event currently fans out to *all* members, so one headline over a
4-member group produces 4 predictions whose outcomes are correlated but scored as independent. This
inflates apparent confidence and is not yet bounded — see
[SyRS §13.3](SyRS-system.md#133-known-limitations).

## 5. Provider routing

No single vendor covers every market, so `provider` is **per asset** and Market Data dispatches
through `AdapterRouter` ([SRS-05 §7.2](SRS-05-market-data.md#7-how-it-works)).

| Provider | Serves | Assets |
|---|---|---|
| `biquote.io` | a curated list of US mega-caps | 12 |
| `yahoo` | Stockholm, Copenhagen, Amsterdam, Paris, Xetra, **and US names biquote lacks** | 25 (incl. 3 fallback entries) |

### 5.1 biquote is limited by its symbol list, not by exchange

Probed 2026-08-03: every European listing returns 0 bars, *including* EU giants that trade on US
exchanges (`ASML`, `SAP`, `NVO`, `SHEL`), and 8 requested US names (`BNTX`, `UAL`, `FANG`, `SPG`,
`VLO`, `ZM`, `MRNA`, `PTON`) also return 0. Those 8 route to Yahoo. `EXCHANGE:TICKER` prefixes are
rejected outright.

### 5.2 Yahoo requires a browser User-Agent

The `HTTP 429`s that retired Yahoo in POC-7 were caused by its custom agent
(`feed-analyzer-poc6/1.0`), **not** by IP rate limiting. Re-probed: 40 concurrent requests with a
browser agent all returned 200, while 8 with curl's default agent all returned 429.

The adapter sends a browser UA and a test asserts the string does not identify this service.

Accept the caveats: the endpoint is undocumented and UA-sniffing may change without notice. That is
precisely why US assets stay on biquote — Yahoo flakiness cannot be allowed to regress existing
scoring, and switching to a paid vendor is one field in the JSON.

### 5.3 Finnhub was evaluated and rejected

Evaluated 2026-08-03 as the paid alternative. Its `/search` does know European listings, but **every
historical-price endpoint returns `403` on the free tier** — including `/stock/candle` for US symbols,
at every resolution. The `60/min` rate limit is not the constraint; endpoint entitlement is.

`/quote` cannot substitute: it returns a live snapshot with no date parameter, and the adapter contract
needs `get_close(asset_id, session)` for specific past sessions.

Viable on a paid plan; confirm `.ST` coverage before subscribing.

### 5.4 Fallback assets

Three primary assets declare a Yahoo fallback, resolving the biquote selective-data gap observed
2026-08-04:

| Primary | Fallback |
|---|---|
| `LMT_NYSE` | `LMT_NYSE_YH` |
| `TSLA_NASDAQ` | `TSLA_NASDAQ_YH` |
| `GOOGL_NASDAQ` | `GOOGL_NASDAQ_YH` |

`AdapterRouter.get_close` retries against the fallback asset's provider when the primary raises
`PriceNotYetAvailableError`.

Fallback entries are named `{PRIMARY_ID}_YH` by convention and carry **empty `keywords`** so they never
participate in article matching — they are invisible to Cleansing and Prediction; only the Market Data
adapter router uses them.

## 6. Session calendars

`shared.calendar` resolves any IANA timezone through stdlib `zoneinfo`. Each asset's `timezone` and
`session_complete_at` drive baseline/settlement resolution, session completion, and Prediction's
market-open stance.

| Market timezone | Local close | UTC (July) |
|---|---|---|
| `America/New_York` | 17:00 | 21:00 |
| `Europe/Stockholm` | 18:00 | 16:00 |
| `Europe/Copenhagen` | 18:00 | 16:00 |
| `Europe/Amsterdam` | 18:00 | 16:00 |
| `Europe/Paris` | 18:00 | 16:00 |
| `Europe/Berlin` (Xetra) | 18:00 | 16:00 |

**DST comes from the tz database, not hardcoded rules.** On 2026-03-10 New York is already `UTC-4`
while Stockholm is still `UTC+1`, so one hand-rolled US DST rule cannot serve both markets — which is
why the hand-rolled arithmetic was replaced with `zoneinfo`.

**Holidays are deliberately not modelled.** A session is any weekday; a local holiday surfaces as a
missing provider bar and the request stays `PENDING` and retries — identical to the existing
US-holiday behaviour. The cost: on a market holiday a request waits out its retry budget rather than
skipping to the next real session. Recorded in
[SyRS §13.2](SyRS-system.md#132-accepted-design-decisions).

## 7. Required reference-series fields

Before an asset may be scored, its registry entry must declare:

- provider and provider symbol;
- instrument type and currency;
- intended economic asset and provider exchange metadata;
- session-date mapping, timezone, and session completion clock;
- `price_kind`, initially either `PROVIDER_DAILY_CLOSE` or `OFFICIAL_SETTLEMENT`;
- whether prices are raw or adjusted;
- continuous-futures rollover policy;
- fallback mapping or explicit `null`.

Provider daily data is `PROVIDER_DAILY_CLOSE`. **It must not be described as an official exchange
settlement** (`SYS-45`).

**Raw (unadjusted) closes only.** Yahoo's `adjclose` is back-adjusted for splits and dividends and
would silently change historical values between fetches, breaking immutable observations.

Current `rollover_policy`: `PROVIDER_MANAGED_CONTINUOUS_INCLUDE_ALL_V1`.

## 8. Registry rules

- Registry changes are reviewed as **contract changes**.
- Adding an asset is **not** a contract change; changing or removing an existing ID **is** —
  predictions, scores, and graph nodes reference IDs by value.
- Canonical market identity and provider bar metadata are recorded separately when they differ.
- A provider timestamp is not assumed to be an official settlement time.
- Fallbacks must represent the same economic instrument, currency, session semantics, and adjustment
  policy.
- An unavailable fallback is `null`; the system must not silently substitute a different listing, ETF,
  or contract.
- Rollover exclusions are declared before outcomes are evaluated.
- API filters accept canonical IDs. UI labels come from the registry.
- **Regenerate the Neo4j seed after any registry edit** (`python scripts/generate-asset-seed.py`).

## 9. Deferred

`USD_INDEX`, `OMXS30`, `SP500` and other index instruments, pending validated provider mappings,
currency, calendars, and fallback quality.

## 10. How to update this document

1. Edit `assets.json` (both the packaged copy and `infra/assets/` if they differ).
2. Run `python scripts/validate-assets.py` — the offline mode alone will not catch a bad ticker.
3. Regenerate the Neo4j asset seed: `python scripts/generate-asset-seed.py`.
4. If a new group was added, seed its group edges or its members will produce no predictions
   (section 4.1).
5. Update the counts in section 1 and in
   [SRS-01 §9.3](SRS-01-shared-foundation.md#93-asset-registry-file).
6. If `registry_version` changed, add a row to section 11.

## 11. Registry version history

- **`multi-market-v2`** (2026-08-06) — 37 assets (34 primary + 3 Yahoo fallback entries for
  `LMT_NYSE`, `TSLA_NASDAQ`, `GOOGL_NASDAQ`). `AdapterRouter.get_close` now retries against the
  fallback asset's provider when the primary raises `PriceNotYetAvailableError`, resolving the
  biquote.io selective data gap observed on 2026-08-04.
- **`multi-market-v2`** (2026-08-04) — 34 assets across US/SE/DK/NL/FR/DE in USD, SEK, EUR, DKK.
  JSON-driven registry, industry groups, per-asset provider routing, per-market calendars.
- **`multi-market-v1`** — first JSON registry; the 15 sector-bellwether IDs (`PHARMA`, `SOFTWARE`, …)
  were replaced by per-company IDs. `GOLD` and `BRENT_OIL` were retained: they carry the seeded Neo4j
  `CAUSES` edges and all existing prediction/score history.
- **`biquote-reference-v1`** (POC-7) — hardcoded Python registry; `XAUUSD`/`UKOIL` plus 15 US sector
  bellwethers, biquote only.
- **`poc6-yahoo-reference-v1`** — retired; `GC=F`/`BZ=F` via the Yahoo chart endpoint. Retired for
  `HTTP 429`s later shown to be a User-Agent problem rather than IP rate limiting.

## 12. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| `2026-08-06` | `1.0.0` | Moved into `requirements/` from `docs/reference/asset-registry.md`. Corrected group count to 16 (both the old document and SRS-01 §9.3 were wrong); added the group list, the full session-calendar table, the fallback-asset table, and update rules | Requirements consolidation |
