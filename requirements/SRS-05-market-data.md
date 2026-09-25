# SRS-05 — Market Data Service

**Document ID:** SRS-05  
**Status:** Implemented  
**Priority:** Must  
**Component prefix:** MKT  
**Related system requirements:** SYS-48 – SYS-55  
**Epic:** E05 (Market Data Service)

---

## Table of Contents

1. [Document Control](#1-document-control)
2. [Purpose and Scope](#2-purpose-and-scope)
3. [Definitions](#3-definitions)
4. [System Context](#4-system-context)
5. [Functional Requirements](#5-functional-requirements)
6. [Non-Functional Requirements](#6-non-functional-requirements)
7. [How It Works](#7-how-it-works)
8. [Interfaces](#8-interfaces)
9. [Data Design](#9-data-design)
10. [Configuration](#10-configuration)
11. [Verification](#11-verification)
12. [Failure Handling](#12-failure-handling)
13. [Assumptions and Limitations](#13-assumptions-and-limitations)
14. [How to Update This Document](#14-how-to-update-this-document)
15. [Change History](#15-change-history)

---

## 1. Document Control

| Field | Value |
|---|---|
| Author | Feed Analyzer project |
| Version | `1.2.0` |
| Created | 2026-08-05 |
| Last updated | 2026-09-25 |
| Replaces | `docs/functional-documents/market-data-service-functional-document.md` (deleted 2026-08-06) |
| Source code | `src/services/market-data/` |
| Config class | `market_data.config.MarketDataSettings` |
| DB schema | `market_data` (owned by this service) |

---

## 2. Purpose and Scope

### 2.1 What this service does

The Market Data Service is responsible for fetching and persisting real price data from external market data providers. It has two distinct roles:

**Role A — Prediction scoring support:** After a `PredictionMade` message is received (via `PriceRequested`), the service fetches the baseline close (the session the prediction was based on) and the settlement close (the next trading session) from a market data provider. Both closes are stored and a `PriceObserved` message is emitted for the Verification Service.

**Role B — Live price reads:** The Prediction Service calls this service's HTTP API to check whether a recent close is price-elevated (the Scope-B gate). This returns the N most recent stored close observations for an asset.

### 2.2 What it does not do

- Does not evaluate prediction accuracy (Verification Service)
- Does not update credibility weights (Credibility Service)
- Does not fetch news (Ingestion Service)
- Does not produce predictions (Prediction Service)
- Does not access Neo4j

---

## 3. Definitions

| Term | Meaning |
|---|---|
| Baseline session | The trading session prior to the prediction (the close the prediction is calibrated against) |
| Settlement session | The trading session whose close is used to score the prediction (next trading day) |
| Session completion | A session is considered complete once the local market has closed: `session_date + local close time (configurable per asset)` |
| Provider | The market data vendor for an asset; determined by the asset registry (`provider` field) |
| biquote.io | Retired 2026-08-29; OHLC JSON API previously used for US mega-caps (see [REF-02 §5.1](REF-02-asset-registry.md)) |
| Yahoo Finance | Provider for every registry asset; chart v8 API with intraday bars requiring timezone shift |
| PriceNotYetAvailableError | Provider returned no settled bar for the requested session (transient; retry scheduled) |
| InvalidObservationError | Provider data failed validation (terminal; request dead-lettered) |
| AdapterUnavailableError | Transport-level provider failure (transient; retry scheduled) |
| Rollover policy | `INCLUDE_ALL` — every positive finite close bar is kept; the POC uses this exclusively |
| content_hash | SHA-256 of `{asset_id}|{session}|{close}|{provider_symbol}|{price_kind}|{is_adjusted}|{registry_version}` — immutability audit trail |
| request_id | The UUID from the `PriceRequested` message; idempotency key for the entire request lifecycle |

---

## 4. System Context

```
[Prediction Service]
       |
       | PriceRequested (routing key: price.requested)
       | Queue: market-data.price-requests
       v
[Market Data Service]
  - register request (idempotent on request_id)
  - fetch baseline close → store CloseObservation
  - wait for settlement session to complete
  - fetch settlement close → store CloseObservation
  - emit PriceObserved
       |
       | PriceObserved (routing key: price.observed)
       v
[Verification Service]

[Prediction Service] ──GET /prices/recent/{asset_id}──> [Market Data Service]
                          (Scope-B price elevation check)

External providers:
  [Yahoo Finance] ← every registry asset (US and European)
```

- Consumes from queue: `market-data.price-requests`
- Publishes to exchange: `feed.events` with routing key `price.observed`
- Exposes HTTP API for read-only recent-close queries
- No Neo4j access
- No LLM calls

---

## 5. Functional Requirements

### Avanza point observations

| ID | Requirement | Status |
|---|---|---|
| MKT-60 | The isolated snapshot worker shall schedule point observations every 900 seconds during each enabled listing's regular exchange session | Implemented |
| MKT-61 | Each sample shall retain the exact decimal price, listing currency, actual observation time and immutable mapping version | Implemented |
| MKT-62 | A failed read shall leave a gap after at most one bounded retry | Implemented |
| MKT-63 | Sample persistence and publication intent shall commit atomically under a fenced job lease | Implemented |
| MKT-64 | Snapshot collection shall preserve existing daily-close and minute-bar API/message semantics | Implemented |
| MKT-65 | Unknown provider quote time shall remain explicitly ineligible for strict sampled verification | Implemented |
| MKT-66 | Close checks shall remain distinct from official daily close observations | Implemented |

### Intraday collection

| ID | Requirement | Status |
|---|---|---|
| MKT-56 | The optional collector shall share one durable price stream per requested asset/session | Implemented |
| MKT-57 | The collector shall publish completed unadjusted minute-bar additions/corrections via an outbox | Implemented |
| MKT-58 | The intraday adapter shall reject wrong listing identity, malformed OHLC and split events | Implemented |
| MKT-59 | Collection shall recover expired leases and stop retrying by its session-anchored deadline | Implemented |

### 5.1 Message consumption

| ID | Requirement | Status |
|---|---|---|
| MKT-1 | The service shall consume `PriceRequested` messages from the `market-data.price-requests` queue | Implemented |
| MKT-2 | On receiving a `PriceRequested` message, the service shall insert a `price_requests` row with state `PENDING` using `ON CONFLICT (request_id) DO NOTHING` so duplicate delivery is a no-op | Implemented |
| MKT-3 | After inserting, the service shall attempt to process the request immediately before returning (no queue-and-wait) | Implemented |

### 5.2 Request lifecycle

| ID | Requirement | Status |
|---|---|---|
| MKT-4 | The service shall manage each price request through the states: `PENDING → BASELINE_OBSERVED → COMPLETED`, or to the terminal state `ABANDONED` | Implemented |
| MKT-5 | The service shall fetch the baseline close first; on success, advance the state to `BASELINE_OBSERVED` and store the `CloseObservation` in `close_observations` | Implemented |
| MKT-6 | Before fetching the settlement close, the service shall check whether the settlement session is complete using the asset's own timezone and configurable session-completion time | Implemented |
| MKT-7 | If the settlement session is not yet complete, the service shall defer the request (record failure, schedule next attempt) and return without fetching | Implemented |
| MKT-8 | Once the settlement session is complete, the service shall fetch the settlement close; on success, the close is stored and `complete_request` is called | Implemented |
| MKT-9 | `complete_request` shall store both observations (idempotent), insert the outbox row, and mark the request `COMPLETED` — all in one transaction | Implemented |
| MKT-10 | If `complete_request` finds the outbox row already exists (prior crash recovery), it shall return `False` and make no further changes | Implemented |

### 5.3 Session completion check

| ID | Requirement | Status |
|---|---|---|
| MKT-11 | Session completeness is `now >= session_completed_at(session, timezone, hour, minute)` where `hour` and `minute` are per-asset registry fields | Implemented |
| MKT-12 | The timezone used shall be the asset's own timezone (e.g. `Europe/Stockholm` for Swedish stocks), not a single global close time | Implemented |
| MKT-13 | The default session-completion time (from `shared.calendar`) shall be 17:00 local time; per-asset overrides are in the registry | Implemented |

### 5.4 Provider routing

| ID | Requirement | Status |
|---|---|---|
| MKT-14 | The service shall route each request to the provider declared in the asset registry's `provider` field | Implemented |
| MKT-15 | An unknown asset ID shall raise `InvalidObservationError` (terminal); the request shall be dead-lettered | Implemented |
| MKT-16 | A valid asset whose declared provider has no configured adapter shall raise `InvalidObservationError` (terminal) | Implemented |
| MKT-17 | Adding a new asset requires only an asset registry edit (registry `provider` field); no code change is needed if the provider already has an adapter | Implemented |

### 5.5 biquote.io adapter

| ID | Requirement | Status |
|---|---|---|
| MKT-18 | The biquote adapter shall call `GET {base_url}/{encoded_symbol}/ohlc?interval=1d&from={start}&to={end}` | Implemented |
| MKT-19 | The fetch window shall span `[session - fetch_window_days, session + fetch_window_days]` to bridge weekends and holidays | Implemented |
| MKT-20 | Bars with `isOpen: true` (still-forming day) shall be excluded; only settled bars are stored | Implemented |
| MKT-21 | The session date for a biquote bar is the calendar date of the UTC-midnight `openTime` (no timezone shift required) | Implemented |
| MKT-22 | A close value that is null, non-finite, or ≤ 0 shall be skipped; all other positive finite closes are kept (INCLUDE_ALL rollover) | Implemented |
| MKT-23 | A transport failure (HTTP error) shall raise `AdapterUnavailableError` (transient) | Implemented |
| MKT-24 | A malformed provider payload (missing `bars`, non-object bar, unparseable `openTime`) shall raise `InvalidObservationError` (terminal) | Implemented |
| MKT-25 | If no settled bar matches the requested session in the returned window, `PriceNotYetAvailableError` shall be raised (transient) | Implemented |

### 5.6 Yahoo Finance adapter

| ID | Requirement | Status |
|---|---|---|
| MKT-26 | The Yahoo adapter shall call `GET {yahoo_base_url}/{encoded_symbol}?interval=1d&range=1mo` with a browser User-Agent to avoid 429 responses | Implemented |
| MKT-27 | The session date for a Yahoo bar shall be derived from the bar's Unix timestamp by converting to the asset's local timezone (the `provider_bar_to_session` shared calendar function) | Implemented |
| MKT-28 | Yahoo bars with a close ≤ 0 or NaN shall be skipped; all others are kept | Implemented |
| MKT-29 | A non-200 response or malformed JSON from Yahoo shall raise `AdapterUnavailableError` (transient) | Implemented |

### 5.7 Close observation storage

| ID | Requirement | Status |
|---|---|---|
| MKT-30 | Each `CloseObservation` shall be stored with `ON CONFLICT (asset_id, session, registry_version) DO NOTHING` so replaying a request uses the existing observation | Implemented |
| MKT-31 | Each observation shall have an immutable `content_hash` computed over key fields for auditable reproducibility (see 7.4) | Implemented |
| MKT-32 | Closes are always stored unadjusted (`is_adjusted = False`); the scoring formula uses the raw provider close | Implemented |
| MKT-33 | The `registry_version` on each observation is the version string from the asset registry at the time of the fetch; version changes produce new observations rather than overwriting old ones | Implemented |

### 5.8 Settlement polling (scheduled job)

| ID | Requirement | Status |
|---|---|---|
| MKT-34 | The service shall run a scheduled job every `SETTLEMENT_POLL_INTERVAL_SECONDS` (default 3600 s = 1 hour) | Implemented |
| MKT-35 | The job shall load requests in state `PENDING` or `BASELINE_OBSERVED` **whose `next_attempt_at` has elapsed** and attempt to advance each one | Implemented |
| MKT-36 | Transient failures shall use exponential backoff: `min(backoff_base × 2^attempts, backoff_max)` seconds, recorded in `next_attempt_at` and honoured by MKT-35 | Implemented |
| MKT-37 | A request still unpriced more than `ABANDON_AFTER_SETTLEMENT_DAYS` after its settlement session shall move to the terminal state `ABANDONED` and never be re-driven | Implemented |
| MKT-37b | A request whose `asset_id` is absent from the loaded registry shall be skipped with a warning rather than raising, so one retired asset cannot abort the tick for every other request | Implemented |

### 5.9 Outbox relay

| ID | Requirement | Status |
|---|---|---|
| MKT-38 | The service shall implement the transactional outbox pattern: the outbox row and state transition are inserted atomically | Implemented |
| MKT-39 | The scheduled job shall sweep `market_data.outbox` for PENDING rows and publish them to `feed.events` with routing key `price.observed` | Implemented |
| MKT-40 | A per-row publish failure shall increment `attempts` and write `last_error` without blocking other rows | Implemented |
| MKT-41 | The outbox table has a UNIQUE constraint on `aggregate_id` (= `request_id`): at most one `PriceObserved` per request | Implemented |

### 5.10 Recent-close HTTP API (Scope-B gate)

| ID | Requirement | Status |
|---|---|---|
| MKT-42 | The service shall expose `GET /prices/recent/{asset_id}?sessions={n}` returning the most recent N `(session, close)` pairs ordered by session descending | Implemented |
| MKT-43 | `sessions` shall be clamped to `[1, 250]`; values outside this range are silently corrected, not rejected | Implemented |
| MKT-44 | If no observations exist for the asset, the endpoint shall return an empty list (not a 404) | Implemented |

### 5.11 Health and readiness

| ID | Requirement | Status |
|---|---|---|
| MKT-45 | The service shall expose `GET /health` returning `{"status": "ok"}` always | Implemented |
| MKT-46 | The service shall expose `GET /ready` returning 200 only when the database pool and RabbitMQ consumer are healthy | Implemented |

---

## 6. Non-Functional Requirements

| ID | Requirement | Status |
|---|---|---|
| MKT-47 | Secrets (`DATABASE_URL`, `RABBITMQ_URL`) shall be environment variables; none committed | Implemented |
| MKT-48 | The service shall bind to the local environment only | Implemented |
| MKT-49 | Close observations are immutable once stored; the `ON CONFLICT DO NOTHING` semantic ensures they are never overwritten | Implemented |
| MKT-50 | Provider calls are made over HTTPS only (biquote.io and Yahoo Finance URLs both use `https://`) | Implemented |
| MKT-51 | The Yahoo adapter uses a browser-style User-Agent to avoid provider-side 429 rate-limiting | Implemented |
| MKT-52 | Settlement polling runs at most once per hour by default; a completed close is picked up within one hour of its publication, which is sufficient for next-trading-day scoring | Implemented |

---

## 7. How It Works

### Avanza snapshot worker

`python -m market_data.snapshots.worker` is a separate process/image owned by Market Data.
The FastAPI process imports only its read-only router and storage module, not Playwright. Collection
is disabled by default; no personal Chrome profile is used. A single PostgreSQL advisory-lock leader
coordinates a reusable browser context with 2 concurrent pages by default, configurable up to 10.
Only explicitly enabled, verified companion mappings are scheduled; discovery never runs per slot.
Mapping identity and versioning are defined in [REF-02](REF-02-asset-registry.md#avanza-companion-mappings).

`exchange_calendars` 4.13.1 resolves exchange-local trading dates, holidays, DST and early closes.
Sessions with a lunch break or a mismatched timezone are unsupported and pause the listing.
Regular slots start at the exchange open and repeat every 15 minutes, strictly before close.
Three bounded close checks are scheduled at close, close+2 minutes and close+5 minutes. There are no
website reads between the final check deadline and the next regular session. The control loop remains
alive to update heartbeat and relay the outbox. Past slots become `MISSED`; restarting never backfills
them with a current quote. Every sample keeps both scheduled and actual observed time.

Each read opens a new tab with Chromium's HTTP cache disabled and service-worker
responses bypassed before navigation; new service workers are also blocked in the browser context.
This applies to temporary and configured persistent profiles, without clearing shared cookies or
disrupting concurrent tabs. Browser cache bypass does not remove the provider's reported quote delay
or prove that an unchanged price is stale. Local browser regression coverage warms a cacheable price
resource, changes the server value and verifies subsequent reads retrieve the new value.

The reader validates URL/instrument ID, title ticker, company heading, visible exchange, instrument
type and currency. It reads only Avanza's regular `Senast betalt` price element, excluding the separate
extended-hours value and chart history. Swedish decimal commas and grouped spaces are parsed into
`Decimal`; unsupported minor-unit prices fail closed. It opens the market-status text panel and
matches the current-state heading, never the colored dot or the timetable legend. Status is
`PRE_OPEN`, `REGULAR_OPEN`, `REGULAR_CLOSED`, `EXTENDED_HOURS`, `HALTED` or `UNKNOWN`.
A disagreement with the scheduled regular session makes evidence `SESSION_MISMATCH`; it does not
invent an open or closed state. Exchange scheduling bounds reads even when the page indicator is unknown.

The currently validated DOM supplies a reported quote delay but no precise provider quote timestamp.
`provider_quote_at` remains null and quality is `FRESHNESS_UNKNOWN`; observation time is not trade time.
An unchanged price does not establish freshness. Close checks remain `CLOSE_UNCONFIRMED`, even when
the regular last price appears final. They cannot populate `close_observations` or one-minute OHLC.
The price-sample event contract belongs to [SRS-01](SRS-01-shared-foundation.md#sampled-price-contract).

### Intraday collector

`IntradayRequested` starts a durable stream when `INTRADAY_ENABLED=true`. Its regular exchange
session boundaries come from Verification. A scheduler polls due streams every 60 seconds. The
Yahoo adapter requests `interval=1m`, `includePrePost=false`, and split/dividend event metadata,
using the browser User-Agent. It requires symbol, currency and timezone identity. Positive finite
OHLC, minute alignment and high/low ordering are validated; null bars remain missing. Incomplete
minutes and extended-hours bars are excluded. Split events invalidate the session.

Registry version must match at registration. Market Data freezes its own full provider reference
series in the stream row; registry edits cannot switch providers/listings midstream. It stores one
current bar map per stream. Each fetch is bounded to the one regular session; this deliberately
revisits earlier minutes to repair gaps. Only additions and changed bars are published as ordered
`IntradayObserved` revisions, shared by every prediction in that session. No per-prediction full
session snapshots are sent. Null/absent bars do not revoke an already observed valid bar.

After close, a five-minute provider buffer applies. Full coverage then finalizes the stream.
Otherwise retry until close plus 24 hours (configurable up to 48); then publish a final incomplete
result. Invalid provider data terminates with an explicit reason. Transient errors retry on the
poll interval, not a hot loop. A new lease lasts five minutes; a stale worker cannot commit after
another worker acquired its lease. Network calls hold no SQL transaction/connection. Bar state,
revision and outbox publication are committed together. Failed publishing leaves the outbox pending;
delivery is at least once and receivers deduplicate stream revisions. Broker outages do not block
collecting provider evidence into the database.

### 7.1 Request lifecycle overview

```
[PriceRequested message arrives]
  ↓
Register request (PENDING) — ON CONFLICT DO NOTHING
  ↓
process(request, now)
  ├── fetch baseline close
  │     ├── success → mark BASELINE_OBSERVED, store CloseObservation
  │     └── PriceNotYetAvailable → defer (schedule next attempt)
  ↓
  ├── check settlement session complete?
  │     ├── not complete → defer
  │     └── complete → fetch settlement close
  │           ├── success → complete_request()
  │           │               store both observations (idempotent)
  │               │           insert outbox row (ON CONFLICT DO NOTHING on aggregate_id)
  │               │           UPDATE state = COMPLETED
  │               └── PriceNotYetAvailable → defer (settlement lag)
  └── InvalidObservationError / AdapterUnavailableError after max_attempts → dead-letter
```

### 7.2 Provider routing

The `AdapterRouter` resolves the provider name from the asset registry:

```python
provider = registry.resolve(asset_id).provider
adapter  = adapters_dict[provider]   # "yahoo"
```

Adapters are instantiated at startup and keyed by name. The provider name in the registry must match
an adapter key; one that does not is a terminal deployment error, not a transient failure.

**Current routing by asset:** every asset routes to `yahoo`. Routing follows the registry `provider`
field, never the exchange, so re-introducing a second vendor is a registry edit plus one adapter.

### 7.3 biquote.io adapter detail

> **Retired 2026-08-29.** `BiquoteAdapter` remains in the codebase and is still registered by name,
> but no registry asset routes to it. Retained for reference; see
> [REF-02 §5.1](REF-02-asset-registry.md) for why.

**API call:**
```
GET https://biquote.io/api/{encoded_symbol}/ohlc
    ?interval=1d
    &from=2024-03-10T00:00:00Z
    &to=2024-03-25T00:00:00Z
```

**Response shape:**
```json
{
  "symbol": "AAPL",
  "interval": "1d",
  "bars": [
    {"openTime": "2024-03-15T00:00:00Z", "open": 170.0, "high": 173.5, "low": 169.2, "close": 172.5, "isOpen": false},
    {"openTime": "2024-03-18T00:00:00Z", "close": null, "isOpen": true}
  ]
}
```

**Parsing rules:**
1. Skip any bar with `"isOpen": true` (still-forming)
2. Skip any bar with null, non-finite, or ≤ 0 close
3. Parse `openTime` as UTC datetime; session date = `openTime.date()`
4. Wrap the valid close as a `CloseObservation` with `source="biquote.io"`, `is_adjusted=False`
5. Return all valid bars as a list; caller picks the one matching the requested session

**Session mapping:** biquote uses UTC-midnight bar times, so the bar's date is directly the trading session date. No timezone conversion needed.

### 7.4 Yahoo Finance adapter detail

**API call:**
```
GET https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}
    ?interval=1d&range=1mo
Headers: User-Agent: Mozilla/5.0 (compatible browser string)
```

The browser User-Agent prevents Yahoo from returning HTTP 429 for automated requests.

**Session mapping:** Yahoo bar timestamps are Unix epoch times in the market's local timezone. The `provider_bar_to_session` shared calendar function converts: `bar_time → local_date_in(bar_time, asset_timezone)`. This is different from biquote, where no shift is needed.

### 7.5 Session completion check

```python
def is_session_complete(session, timezone_name, *, now, hour, minute):
    completed_at = session_completed_at(session, timezone_name, hour=hour, minute=minute)
    return now >= completed_at
```

`session_completed_at(session, tz, hour, minute)` builds a timezone-aware datetime from the session date and local close time. For Stockholm (Europe/Stockholm), a 17:00 close on a summer day is 15:00 UTC. For New York (America/New_York), 17:00 is 22:00 UTC.

This is called before the settlement fetch, not after. A request that arrives before the session is complete will be deferred and re-attempted by the hourly polling job.

### 7.6 Exponential backoff

```python
backoff_seconds = min(backoff_base × 2^attempts, backoff_max)
# defaults: base=2.0, max=8.0
# attempt 0: min(2.0 × 1, 8.0)  = 2.0 s
# attempt 1: min(2.0 × 2, 8.0)  = 4.0 s
# attempt 2: min(2.0 × 4, 8.0)  = 8.0 s
# attempt 3+: 8.0 s (capped)
```

A request still unpriced more than `ABANDON_AFTER_SETTLEMENT_DAYS` (default 7) after its settlement
session moves to the terminal `ABANDONED` state. The bound is calendar age rather than attempt count,
because waiting for a settlement session to complete legitimately consumes many polling attempts,
while a bar absent a week after settlement is missing rather than late.

Note: in practice, the hourly polling interval dominates the retry cadence; the backoff only matters for rapid-fire failures within one poll cycle.

### 7.7 Content hash

```
hash_input = "{asset_id}|{session}|{close_exact}|{provider_symbol}|{price_kind}|{is_adjusted}|{registry_version}"
content_hash = SHA-256(hash_input.encode("utf-8")).hexdigest()
```

`close_exact` uses Python's `format(close, "f")` (full decimal representation). This hash is stored immutably alongside the observation as an audit trail; it is not used for lookup.

### 7.8 Worked example

**Scenario:** A prediction was made for AAPL on 2024-03-15 (baseline) for settlement on 2024-03-18 (Monday).

1. `PriceRequested` arrives at 2024-03-15T11:00Z with `baseline_session=2024-03-15`, `settlement_session=2024-03-18`
2. Request inserted as PENDING
3. Immediate processing:
   - Fetch baseline (2024-03-15): biquote bar available → `CloseObservation(session=2024-03-15, close=170.50, ...)`
   - Mark BASELINE_OBSERVED
   - Check settlement (2024-03-18) complete: now=2024-03-15T11:00Z < 2024-03-18T22:00Z (17:00 ET) → not complete → defer
4. Hourly poll at 2024-03-18T14:00Z:
   - Settlement check: 14:00 < 22:00 UTC → not complete → defer again
5. Hourly poll at 2024-03-18T23:00Z:
   - Settlement check: 23:00 ≥ 22:00 UTC → complete
   - Fetch settlement (2024-03-18): biquote bar `close=173.20` → `CloseObservation(session=2024-03-18, ...)`
   - `complete_request`: store both observations, insert outbox row with `PriceObserved`, mark COMPLETED
6. Outbox sweep: publish `PriceObserved` to `feed.events`

---

## 8. Interfaces

Snapshot endpoints are additive and available independently of the browser worker:

| Endpoint | Response |
|---|---|
| `GET /snapshots/status` | `NOT_STARTED` before worker schema initialization; otherwise durable control status/heartbeat, asset pauses/errors, session window including next open, market state/close confirmation, job counts and pending outbox count |
| `GET /snapshots/recent?asset_id=…&limit=100&before=…` | Newest-first `PriceSampleObserved` objects; decimal prices serialize as strings; limit 1–1000; optional timezone-aware timestamp is an exclusive upper bound; empty before initialization |

`/prices/recent`, `/health`, `/ready`, `PriceObserved` and `IntradayObserved` retain their previous
meaning and response fields. Collector readiness is visible through `/snapshots/status` rather than
making legacy API readiness depend on a browser. Currency and market-state metadata are exposed on
the new snapshot endpoints/event only.

Optional intraday consumer: `market-data.intraday-requests`, bound to `intraday.requested`.
Producer: `intraday.observed`, consumed only by shadow verification. Contracts and topology are in
[SRS-01](SRS-01-shared-foundation.md#84-rabbitmq-topology). `/health.intraday_enabled` reports the flag.

### 8.1 Consumed message

**Queue:** `market-data.price-requests`  
**Type:** `PriceRequested` (see SRS-01, section 5.7)

Key fields used:
- `request_id` — idempotency key for the entire lifecycle
- `prediction_id` — propagated to `PriceObserved` for Verification's lookup
- `asset_id` — determines provider and timezone
- `baseline_session` — date to fetch first
- `settlement_session` — date to fetch after session completion
- `market_calendar` — stored on the request row (informational; timezone comes from registry)
- `correlation_id` — propagated to `PriceObserved`

### 8.2 Published message

**Exchange:** `feed.events`  
**Routing key:** `price.observed`  
**Type:** `PriceObserved` (see SRS-01, section 5.8)

Key fields set by this service:
- `message_id` — new UUID
- `request_id` — from the `PriceRequested` message
- `prediction_id` — from the `PriceRequested` message
- `asset_id` — the asset
- `baseline` — `CloseObservation` for the baseline session
- `settlement` — `CloseObservation` for the settlement session
- `correlation_id` — from the `PriceRequested` message
- `causation_id` — the `request_id`

### 8.3 HTTP endpoints

| Method | Path | Query params | Description |
|---|---|---|---|
| GET | `/health` | — | Returns `{"status": "ok"}` always |
| GET | `/ready` | — | Returns 200 if DB pool and RabbitMQ consumer healthy |
| GET | `/prices/recent/{asset_id}` | `sessions={n}` (default 10) | Returns N most recent `(session, close)` pairs for the asset, ordered by session DESC |

### 8.4 Scheduled jobs

| Job | Interval | What it does |
|---|---|---|
| `settlement_poll` | 3600 s (1 hour, configurable) | Loads all non-COMPLETED requests, attempts to advance each; sweeps outbox |

---

## 9. Data Design

The idempotent DDL in `market_data/snapshots/storage.py` adds the following tables; it alters no
existing table. All timestamps are `TIMESTAMPTZ`; serialized models retain Decimal strings.

| Table (`market_data` schema) | Columns and constraints |
|---|---|
| `snapshot_mappings` | `version` PK, SHA-256 `content_hash`, immutable typed config `payload` |
| `snapshot_assets` | `asset_id` PK, current `version`, `enabled`, `paused`, consecutive `failures`, `last_error`, `last_success` |
| `snapshot_control` | singleton `id=1`, durable `cooldown_until`, `paused`, `failure_count`, `status`, `heartbeat` |
| `snapshot_sessions` | PK `(asset_id,session,version)`, frozen `session_window` JSON text, `market_state`, `state_at`, `status_text`, `final_sample`, `close_status` |
| `snapshot_jobs` | deterministic UUID `job_id` PK, `asset_id`, `session`, mapping FK `version`, `registry_version`, frozen `listing`/`session_window`, `scheduled_at`, `kind`, `state`, `attempts`, `next_attempt_at`, `lease`, `lease_until`, `last_error`; unique `(asset_id,session,version,scheduled_at,kind)` and pending-due index |
| `price_samples` | `sample_id` PK/FK to job, `asset_id`, `session`, mapping FK `mapping_version`, `registry_version`, `instrument_id`, `exchange`, positive NUMERIC `price`, `currency`, `quote_unit`, `scheduled_at`, `observed_at`, nullable `provider_quote_at`, `kind`, `quality`, immutable event `payload`; asset/time index |
| `snapshot_outbox` | `message_id` PK, immutable event `payload`, `delivered`, `attempts`, `next_attempt_at`, `created_at` |

Jobs use `FOR UPDATE SKIP LOCKED` with 90-second UUID leases. Commit requires the current lease and
an enabled, unpaused mapping of the same version. Price and outbox insert in one transaction.
Broker publication is at least once; its stable message/sample IDs let consumers deduplicate.
No retention runs automatically; capacity limits pause reads, preserving evidence for
[E11 retention design](../backlog/E11-Data-Retention/README.md#snapshot-evidence-retention).

Intraday DDL is idempotently applied at startup:

| Table | Columns / invariants |
|---|---|
| `market_data.intraday_streams` | `stream_id` primary key; immutable `request` and frozen `series`; current JSONB `bars`; monotonically increasing `revision`; `done`, `lease`, `next_attempt_at`, `last_error`, `updated_at`; partial due-work index |
| `market_data.intraday_outbox` | `message_id` primary key, immutable serialized `payload`, `delivered`, `created_at`; retains delta history for audit |

### 9.1 Table: `market_data.price_requests`

| Column | Type | Notes |
|---|---|---|
| `request_id` | UUID PRIMARY KEY | From `PriceRequested`; idempotency key |
| `prediction_id` | UUID NOT NULL | Prediction this request is scoring |
| `asset_id` | TEXT NOT NULL | Registry asset ID |
| `baseline_session` | DATE NOT NULL | Session to fetch first |
| `settlement_session` | DATE NOT NULL | Session to fetch after completion |
| `market_calendar` | TEXT NOT NULL | Calendar string from the message (informational) |
| `correlation_id` | UUID NOT NULL | Propagated to `PriceObserved` |
| `state` | TEXT DEFAULT 'PENDING' | `PENDING`, `BASELINE_OBSERVED`, `COMPLETED`, or `ABANDONED` |
| `attempts` | INTEGER DEFAULT 0 | Total fetch attempts (incremented on each deferred/completed) |
| `next_attempt_at` | TIMESTAMPTZ | Earliest time for the next processing attempt |
| `last_error` | TEXT | Most recent failure message |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ DEFAULT now() | Updated on each state change |

Index: `ix_price_requests_open ON (next_attempt_at) WHERE state IN ('PENDING', 'BASELINE_OBSERVED')` — partial index, only unfinished requests visible.

### 9.2 Table: `market_data.close_observations`

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL PRIMARY KEY | Row identity |
| `asset_id` | TEXT NOT NULL | Registry asset ID |
| `session` | DATE NOT NULL | Trading session date |
| `close` | NUMERIC NOT NULL CHECK (close > 0) | Raw unadjusted close price |
| `provider_bar_time` | TIMESTAMPTZ | Provider-supplied bar timestamp (nullable for Yahoo) |
| `fetched_at` | TIMESTAMPTZ NOT NULL | When this row was written |
| `source` | TEXT NOT NULL | `"biquote.io"` or `"yahoo"` |
| `provider_symbol` | TEXT NOT NULL | Symbol used with the provider (from registry) |
| `price_kind` | TEXT NOT NULL | `PROVIDER_DAILY_CLOSE` |
| `is_adjusted` | BOOLEAN NOT NULL | Always `false` in this POC |
| `registry_version` | TEXT NOT NULL | Asset registry version at fetch time |
| `content_hash` | TEXT NOT NULL | SHA-256 audit hash |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| UNIQUE | `(asset_id, session, registry_version)` | Prevents duplicate observations; enables idempotent replay |

Index: `ix_close_observations_recent ON (asset_id, session DESC)` — supports the recent-close read query.

### 9.3 Table: `market_data.outbox`

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL PRIMARY KEY | Insertion order |
| `message_id` | UUID NOT NULL UNIQUE | Idempotency key for outbox row |
| `aggregate_id` | UUID NOT NULL UNIQUE | = `request_id`; ensures one `PriceObserved` per request |
| `routing_key` | TEXT NOT NULL | `price.observed` |
| `payload` | JSONB NOT NULL | Full `PriceObserved` message |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| `delivery_status` | TEXT DEFAULT 'PENDING' | `PENDING` or `DELIVERED` |
| `attempts` | INTEGER DEFAULT 0 | |
| `last_error` | TEXT | |
| `delivered_at` | TIMESTAMPTZ | |

Index: `ix_market_data_outbox_pending ON (created_at) WHERE delivery_status = 'PENDING'` — partial index.

Note: both `message_id` and `aggregate_id` have UNIQUE constraints. The `aggregate_id` constraint (keyed on `request_id`) is the primary idempotency guard — at most one `PriceObserved` per price request.

---

## 10. Configuration

### Snapshot process configuration

All keys below apply only to the optional worker. `DATABASE_URL` and `RABBITMQ_URL` are the unprefixed
shared connection strings. The companion config is mounted read-only with the canonical registry.

| Environment variable | Default | Meaning |
|---|---|---|
| `SNAPSHOT_ENABLED` | `false` | Run collection only when true |
| `SNAPSHOT_MAPPINGS_PATH` | `infra/assets/avanza-listings.json` | Companion file; Compose uses `/config/avanza-listings.json` |
| `SNAPSHOT_CONCURRENCY` | `2` | Page limit 1–10; raise only after capacity measurement |
| `SNAPSHOT_INTERVAL_SECONDS` | `900` | v1 accepts only 900 |
| `SNAPSHOT_TIMEOUT_SECONDS` | `30` | Total read budget, 1–30 seconds |
| `SNAPSHOT_RETRY_SECONDS` | `10` | Retry delay 0–20, plus 0–3 seconds jitter |
| `SNAPSHOT_MAX_LATENESS_SECONDS` | `120` | Slot deadline, configurable 30–120 seconds |
| `SNAPSHOT_MAX_QUOTE_AGE_SECONDS` | `120` | Eligibility threshold when provider timestamp is known; 1–900 seconds |
| `SNAPSHOT_MAX_PENDING_EVENTS` | `10000` | Stop new reads at this outbox backlog |
| `SNAPSHOT_MAX_SAMPLES` | `1000000` | Stop new reads at this stored sample count |
| `SNAPSHOT_BROWSER_CHANNEL` | unset | Bundled Chromium; local `chrome` may be selected |
| `SNAPSHOT_PROFILE_PATH` | unset | Optional dedicated manually provisioned profile; never personal Chrome data |
| `SNAPSHOT_LOG_LEVEL` | `INFO` | Worker log level |

### Snapshot deployment and recovery

The optional Compose profile `snapshots` builds `Dockerfile.snapshots` with hash-pinned Playwright
dependencies and its matching Chromium. It runs as UID 10001 with 2 CPUs, 3 GiB memory, 1 GiB shared
memory and a 512-process limit. Organizations using a private root CA must install it in the base
image; the browser downloader uses the system CA bundle and the Dockerfile imports the base image's
local CAs into Chromium's NSS store. See [Chromium certificate management](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/linux/cert_management.md). TLS verification remains enabled.

After the access/freshness and pilot gates below, enable a small reviewed listing subset in a **new**
mapping version, set `SNAPSHOT_ENABLED=true` in the local environment, then run:

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml --profile snapshots up -d --build feed-market-data-snapshots
```

Deploy the API image to expose the new read endpoints and the Verification image for optional sampled
reports. Neither requires enabling the collector. Roll back collection with `docker compose --env-file
infra/.env -f infra/docker-compose.yml --profile snapshots stop feed-market-data-snapshots`; set the
sample policy OFF. All prior evidence and legacy operations remain available.

Mappings reload each cycle; changed contents under an existing version are rejected. A corrupt edit
keeps the last valid mapping and exposes `CONFIG_ERROR`. Repair a paused listing with a reviewed new
mapping version. After resolving a provider access restriction, stop the worker, run a manual probe,
then explicitly reset `market_data.snapshot_control` with `paused=false`, `cooldown_until=NULL`,
`failure_count=0`, `status='STARTING'` for `id=1`, and restart. No automatic login/CAPTCHA bypass occurs.

| Intraday variable | Default | Effect |
|---|---|---|
| `INTRADAY_ENABLED` | `false` | Enables only the shadow collector; Compose maps `MARKET_DATA_INTRADAY_ENABLED` |
| `INTRADAY_REQUESTS_QUEUE` | `market-data.intraday-requests` | Owned work queue; default topology name |
| `INTRADAY_POLL_SECONDS` | `60` | Poll/retry interval; Compose maps `MARKET_DATA_INTRADAY_POLL_SECONDS` |
| `INTRADAY_FINALIZATION_SECONDS` | `300` | Minimum delay after actual exchange close before complete data finalizes |
| `INTRADAY_RETRY_HOURS` | `24` | Final deadline after close; range 1–48 hours |

No `MARKET_DATA_` prefix; this service uses unprefixed variables directly (`MarketDataSettings` has `model_config = SettingsConfigDict(extra="ignore")` with no `env_prefix`).

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | `postgresql://feed_user:local_dev_pw@localhost:5432/feed` | PostgreSQL connection string |
| `RABBITMQ_URL` | `amqp://feed_user:local_dev_pw@localhost:5672/` | RabbitMQ connection string |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `PRICE_REQUESTS_QUEUE` | `market-data.price-requests` | Queue to consume from |
| `BIQUOTE_BASE_URL` | `https://biquote.io/api` | biquote.io API base URL |
| `YAHOO_BASE_URL` | `https://query1.finance.yahoo.com/v8/finance/chart` | Yahoo Finance chart v8 base URL |
| `PROVIDER_TIMEOUT_SECONDS` | `30.0` | HTTP timeout for all provider calls |
| `FETCH_WINDOW_DAYS` | `10` | Calendar days on each side of the session to fetch (bridges weekends/holidays) |
| `SETTLEMENT_POLL_INTERVAL_SECONDS` | `3600` | How often the settlement polling job runs |
| `RETRY_BACKOFF_BASE_SECONDS` | `2.0` | Exponential backoff base |
| `RETRY_BACKOFF_MAX_SECONDS` | `8.0` | Exponential backoff cap |
| `ABANDON_AFTER_SETTLEMENT_DAYS` | `7` | Days after the settlement session before an unpriced request is abandoned |
| `DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |

---

## 11. Verification

Snapshot coverage:

| Requirements | Proving checks |
|---|---|
| MKT-60, MKT-61, MKT-62, MKT-65, MKT-66 | `tests/test_snapshots.py`: currency parsing, status text, holiday/early close, close-check slots, unknown timestamp, bounded retries, expired deadlines and SQL retry timestamp preservation |
| MKT-63 | `tests/test_snapshot_storage.py`: real PostgreSQL repeatable DDL, concurrent claims, expired-lease fencing, immutable mapping history, atomic sample/outbox and broker replay |
| MKT-64 | `tests/test_snapshot_api.py`, existing `tests/test_recent_closes.py`, shared message suites; snapshot control failure does not change legacy responses |

Unit tests use synthetic identities, not assumptions about the production asset registry.

Intraday adapter requirements MKT-57–MKT-58 are proved by `tests/test_intraday_adapter.py`:
completed bars, null gaps, browser header, identity, split events, OHLC bounds, array lengths and 429.
MKT-56/MKT-57/MKT-59 are exercised with real PostgreSQL in Verification's
`tests/test_intraday_persistence.py` (shared stream, delta outbox, duplicate requests and expired lease).
The database tests require an explicitly configured disposable `INTRADAY_TEST_DATABASE_URL`.

| Requirement | Test file | What is verified |
|---|---|---|
| MKT-1 – MKT-3 (request registration) | `tests/test_storage.py` | Idempotent insert; PENDING state |
| MKT-4 – MKT-10 (lifecycle) | `tests/test_handler.py` | PENDING → BASELINE_OBSERVED → COMPLETED transitions; deferred on incomplete session |
| MKT-11 – MKT-13 (session completion) | `tests/test_sessions.py` | Stockholm vs New York close times; is_session_complete boundary |
| MKT-14 – MKT-17 (provider routing) | `tests/test_router.py` | Known asset → correct adapter; unknown asset → InvalidObservationError |
| MKT-18 – MKT-25 (biquote adapter) | `tests/test_biquote.py` | Valid bars; isOpen skip; null close skip; PriceNotYetAvailable; AdapterUnavailable; InvalidObservation |
| MKT-26 – MKT-29 (Yahoo adapter) | `tests/test_yahoo.py` | Session timezone shift; User-Agent header; invalid JSON handling |
| MKT-30 – MKT-33 (observation storage) | `tests/test_storage.py` | Idempotent upsert; content_hash computation; is_adjusted=False |
| MKT-34 – MKT-37b (polling, backoff, abandonment) | `tests/test_handler.py` | Backoff formula; abandonment past the grace window; no abandonment inside it; a late bar still completes |
| MKT-42 – MKT-44 (recent-close API) | `tests/test_app.py` | Correct order; clamp to 1–250; empty list when no data |
| End-to-end | `tests/test_integration.py` | Full PriceRequested → PriceObserved flow |

---

## 12. Failure Handling

Snapshot failures are isolated from the daily/minute collectors:

Worker logs identify each attempted job, its scheduled/start times and attempt number. Failed reads
include the error type and wall-clock/monotonic elapsed seconds to investigate execution gaps;
saved observations include their observation time, currency and quality. Tick, save and outbox
errors log exception types without connection strings or provider payloads. A workstation or Docker
VM suspension can lose collection slots; recovery records gaps rather than backfilling observations.

| Snapshot failure | Action |
|---|---|
| Timeout/transport/temporary provider failure | One retry if it fits the slot deadline; terminal failure records a gap and continues other assets |
| Wrong identity/currency/type, removed listing or unexpected redirect | Pause that mapping; no sample saved |
| Repeated markup/parse failure | Pause mapping after three terminal failures; require reviewed mapping repair |
| HTTP 429 | Provider-wide cooldown respecting `Retry-After`, default 15 minutes; single recovery probe |
| HTTP 401/403 or explicit challenge title | Durable provider pause `ACTION_REQUIRED`; manual access repair |
| Five consecutive terminal transport/provider failures | Provider-wide 15-minute circuit cooldown |
| Browser crash | One browser restart per batch; job attempts/deadline still bound work |
| SQL save failure | Retry the same captured observation once; never reread and assign the earlier timestamp |
| Broker outage | Persist outbox; retry publication after 30 seconds; collector pauses at backlog cap |
| Restart after missed slot | Mark missed; no historical fabrication |
| Mapping disabled or changed during a read | Fenced commit rejects obsolete job |
| Missing quote timestamp or unknown/closed session | Preserve labeled observation; strict verification excludes it |
| Last close check unavailable | `CLOSE_UNCONFIRMED`; next website read waits for next regular session |

| Failure scenario | Behaviour |
|---|---|
| Duplicate `PriceRequested` (same request_id) | `ON CONFLICT DO NOTHING` at insert; no second schedule, no second observation |
| Baseline not yet available (provider 404 / no bar) | `PriceNotYetAvailableError`; deferred with exponential backoff |
| Settlement session not yet complete | Deferred immediately; re-attempted next polling cycle |
| Settlement not yet available after session complete | `PriceNotYetAvailableError`; deferred (settlement lag) |
| Provider returns malformed data | `InvalidObservationError`; request dead-lettered (no retry) |
| Provider transport error (HTTP timeout, connection reset) | `AdapterUnavailableError`; deferred with backoff |
| Max attempts reached | Request dead-lettered; last_error recorded; no further polling |
| Unknown asset ID | `InvalidObservationError` (terminal) from router; dead-lettered |
| Unknown provider for a valid asset | `InvalidObservationError` (terminal); dead-lettered |
| `complete_request` finds outbox row already exists | Returns `False`; state not changed twice; safe on crash replay |
| Outbox publish failure | `attempts` incremented, `last_error` written; retried next sweep |
| Database connection lost mid-processing | Transaction rolls back; state unchanged; re-processed next polling cycle |

---

## 13. Assumptions and Limitations

Snapshot rollout remains gated. Anonymous Chrome probes on 2026-09-24 UTC validated 29 disabled
listing mappings and regular-price parsing across USD/SEK/EUR/DKK. The built Linux image also read
all 29 listings successfully with the same currency checks and TLS verification enabled. Pages reported
900-second delay and exposed no exact quote timestamp. Read success therefore does **not** prove
freshness or improve strict verification accuracy. Current collector output remains observational.
No official-close promotion is implemented. Confirm unattended access/entitlement for the intended
deployment and run at least three trading sessions on 2–4 listings before expansion. Measure read
success separately from freshness eligibility, with a proposed >=95% scheduled-read success gate,
no wrong identities/currencies, explained gaps and measured memory/latency within the slot budget.

### 13.1 Accepted design decisions

| Decision | Rationale |
|---|---|
| Per-asset provider routing via registry | Avoids hardcoding which stocks come from which vendor; adding an asset requires only a registry edit |
| Session completion uses asset's own timezone | A Stockholm stock closes hours before New York; waiting for a global close time would delay European scores unnecessarily |
| Hourly polling interval | Daily closes settle once; hourly polls ensure the score is available within one hour of settlement; sub-hourly would add traffic without benefit |
| Unadjusted closes only | The scoring formula uses a ratio (`return = (settlement - baseline) / baseline`); split-adjusted prices would change the ratio for older predictions; the POC avoids this complexity |
| INCLUDE_ALL rollover for all assets | The POC does not handle futures rolls, dividend adjustments, or delisting; every positive finite close is kept as-is |
| Yahoo User-Agent browser string | Yahoo's API returns HTTP 429 for known automated agents; the browser string is necessary for the service to function |
| biquote replaces Yahoo for US mega-caps (POC-7) | **Reversed 2026-08-29.** biquote offered a stable, documented API with no anti-scraping measures, but proved to be a quote/CFD feed omitting ~40% of trading sessions; all assets moved back to Yahoo |

### 13.2 Known limitations

- **No bar-time validation beyond the date** — the service checks only that a bar's session date matches the requested date; it does not validate that the bar is truly the official daily close (e.g. closing auction price vs. last trade).
- **Settlement lag is bounded by calendar age, not attempt count** — a request unpriced more than `ABANDON_AFTER_SETTLEMENT_DAYS` (default 7) after its settlement session is abandoned and its prediction is never scored. A provider outage longer than the window therefore drops those predictions rather than retrying indefinitely.
- **No holiday modelling** — the session completion check uses only the asset's daily close time; public holidays are not recognised. A holiday produces no provider bar, which is treated as `PriceNotYetAvailableError` and deferred.
- **Recent-close API serves all stored closes** — the `/prices/recent/{asset_id}` endpoint returns the `N` most recent observations without any date-range filter. If registry_version changes frequently, older observations for a different version may appear alongside current ones.
- **Browser User-Agent for Yahoo may need updating** — if Yahoo detects and blocks the current UA string, the Yahoo adapter will fail with `AdapterUnavailableError`. Since 2026-08-29 Yahoo serves every asset, so this halts all price observation, not just European assets.
- **A missing session is indistinguishable from a late one** — both surface as `PriceNotYetAvailableError` and are retried. A session a provider will never publish therefore retries indefinitely.

---

## 14. How to Update This Document

### 14.1 When to update

Update this document whenever any of the following changes:

- A new provider adapter is added (add a row to sections 7.2 and 5.5/5.6)
- The provider routing rules change in the asset registry (update section 7.2 and the limitation note)
- The session completion logic changes (update section 7.5 and 5.3)
- The `PriceRequested` or `PriceObserved` message schema changes (update section 8)
- A new HTTP endpoint is added or an existing one changes
- A new table column is added or modified in `db.py`
- An environment variable is added, removed, or has its default changed in `config.py`
- The backoff formula changes
- A new test file is added (add it to section 11)
- An accepted design decision is revisited (especially unadjusted closes or rollover policy)

### 14.2 Steps to update

1. **Read the current source first** — verify behaviour before writing requirements
2. **Assign the next MKT-N ID** — check the highest existing ID and continue the sequence
3. **Update the relevant section**
4. **Add a row to section 15** (Change History) with date, what changed, and why
5. **Do not renumber existing IDs** — mark removed requirements as `Status: Withdrawn`
6. **Update `requirements/README.md`** if the ID range for MKT changes

---

## 15. Change History

| Date | Description |
|---|---|
| 2026-09-25 | Add isolated Avanza 15-minute snapshots, durable jobs/outbox, additive APIs and disabled rollout; MKT-60–MKT-66 |
| 2026-09-24 | v1.1.0: optional durable intraday stream collector, strict Yahoo minute evidence, bounded retries and delta outbox |
| 2026-08-05 | Initial as-built specification for E05 (Market Data Service); MKT-1 through MKT-52 |
