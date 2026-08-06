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
| Created | 2026-08-05 |
| Last updated | 2026-08-05 |
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
| biquote.io | Provider for US mega-cap stocks; OHLC JSON API with UTC-midnight bar times |
| Yahoo Finance | Provider for European/remaining stocks; chart v8 API with intraday bars requiring timezone shift |
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
  [biquote.io]    ← US mega-cap stocks
  [Yahoo Finance] ← European / remaining stocks
```

- Consumes from queue: `market-data.price-requests`
- Publishes to exchange: `feed.events` with routing key `price.observed`
- Exposes HTTP API for read-only recent-close queries
- No Neo4j access
- No LLM calls

---

## 5. Functional Requirements

### 5.1 Message consumption

| ID | Requirement | Status |
|---|---|---|
| MKT-1 | The service shall consume `PriceRequested` messages from the `market-data.price-requests` queue | Implemented |
| MKT-2 | On receiving a `PriceRequested` message, the service shall insert a `price_requests` row with state `PENDING` using `ON CONFLICT (request_id) DO NOTHING` so duplicate delivery is a no-op | Implemented |
| MKT-3 | After inserting, the service shall attempt to process the request immediately before returning (no queue-and-wait) | Implemented |

### 5.2 Request lifecycle

| ID | Requirement | Status |
|---|---|---|
| MKT-4 | The service shall manage each price request through the states: `PENDING → BASELINE_OBSERVED → COMPLETED` | Implemented |
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
| MKT-35 | The job shall load all requests not in state `COMPLETED` from the database and attempt to advance each one | Implemented |
| MKT-36 | Transient failures shall use exponential backoff: `min(backoff_base × 2^attempts, backoff_max)` seconds | Implemented |
| MKT-37 | After `max_fetch_attempts` failures, the request shall be marked as dead-lettered (terminal error) | Implemented |

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
adapter  = adapters_dict[provider]   # "biquote" or "yahoo"
```

The two adapters are instantiated at startup and keyed by name. The provider name in the registry (`"biquote"` or `"yahoo"`) must match an adapter key.

**Current routing by asset:**
- `biquote` — US mega-cap stocks: AAPL, MSFT, AMZN, GOOG, etc.
- `yahoo` — European markets (Stockholm, Euronext, Xetra) and any US names not covered by biquote

### 7.3 biquote.io adapter detail

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

After `max_fetch_attempts` (default 3) failed attempts, the request is dead-lettered.

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
| `state` | TEXT DEFAULT 'PENDING' | `PENDING`, `BASELINE_OBSERVED`, or `COMPLETED` |
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
| `MAX_FETCH_ATTEMPTS` | `3` | Attempts before a request is dead-lettered |
| `DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |

---

## 11. Verification

| Requirement | Test file | What is verified |
|---|---|---|
| MKT-1 – MKT-3 (request registration) | `tests/test_storage.py` | Idempotent insert; PENDING state |
| MKT-4 – MKT-10 (lifecycle) | `tests/test_handler.py` | PENDING → BASELINE_OBSERVED → COMPLETED transitions; deferred on incomplete session |
| MKT-11 – MKT-13 (session completion) | `tests/test_sessions.py` | Stockholm vs New York close times; is_session_complete boundary |
| MKT-14 – MKT-17 (provider routing) | `tests/test_router.py` | Known asset → correct adapter; unknown asset → InvalidObservationError |
| MKT-18 – MKT-25 (biquote adapter) | `tests/test_biquote.py` | Valid bars; isOpen skip; null close skip; PriceNotYetAvailable; AdapterUnavailable; InvalidObservation |
| MKT-26 – MKT-29 (Yahoo adapter) | `tests/test_yahoo.py` | Session timezone shift; User-Agent header; invalid JSON handling |
| MKT-30 – MKT-33 (observation storage) | `tests/test_storage.py` | Idempotent upsert; content_hash computation; is_adjusted=False |
| MKT-34 – MKT-37 (polling + backoff) | `tests/test_handler.py` | Backoff formula; max_attempts dead-letter |
| MKT-42 – MKT-44 (recent-close API) | `tests/test_app.py` | Correct order; clamp to 1–250; empty list when no data |
| End-to-end | `tests/test_integration.py` | Full PriceRequested → PriceObserved flow |

---

## 12. Failure Handling

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

### 13.1 Accepted design decisions

| Decision | Rationale |
|---|---|
| Per-asset provider routing via registry | Avoids hardcoding which stocks come from which vendor; adding an asset requires only a registry edit |
| Session completion uses asset's own timezone | A Stockholm stock closes hours before New York; waiting for a global close time would delay European scores unnecessarily |
| Hourly polling interval | Daily closes settle once; hourly polls ensure the score is available within one hour of settlement; sub-hourly would add traffic without benefit |
| Unadjusted closes only | The scoring formula uses a ratio (`return = (settlement - baseline) / baseline`); split-adjusted prices would change the ratio for older predictions; the POC avoids this complexity |
| INCLUDE_ALL rollover for all assets | The POC does not handle futures rolls, dividend adjustments, or delisting; every positive finite close is kept as-is |
| Yahoo User-Agent browser string | Yahoo's API returns HTTP 429 for known automated agents; the browser string is necessary for the service to function |
| biquote replaces Yahoo for US mega-caps (POC-7) | biquote provides a stable, documented API with no anti-scraping measures; Yahoo requires a browser User-Agent workaround |

### 13.2 Known limitations

- **No bar-time validation beyond the date** — the service checks only that a bar's session date matches the requested date; it does not validate that the bar is truly the official daily close (e.g. closing auction price vs. last trade).
- **Settlement lag not bounded** — if a provider delays publishing a bar, the request will keep retrying up to `max_fetch_attempts` times. For end-of-month events where a provider is slow, this may result in the prediction never being scored.
- **No holiday modelling** — the session completion check uses only the asset's daily close time; public holidays are not recognised. A holiday produces no provider bar, which is treated as `PriceNotYetAvailableError` and deferred.
- **Recent-close API serves all stored closes** — the `/prices/recent/{asset_id}` endpoint returns the `N` most recent observations without any date-range filter. If registry_version changes frequently, older observations for a different version may appear alongside current ones.
- **Browser User-Agent for Yahoo may need updating** — if Yahoo detects and blocks the current UA string, the Yahoo adapter will fail with `AdapterUnavailableError` for all European assets.
- **biquote.io API key not required in POC** — the current biquote adapter makes unauthenticated requests; if biquote adds authentication requirements, an API key field must be added to the config.

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
| 2026-08-05 | Initial as-built specification for E05 (Market Data Service); MKT-1 through MKT-52 |
