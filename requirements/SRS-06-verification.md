# SRS-06 — Verification Service

**Document ID:** SRS-06  
**Status:** Implemented  
**Priority:** Must  
**Component prefix:** VER  
**Related system requirements:** SYS-56 – SYS-63  
**Epic:** E06 (Verification Service)

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
| Replaces | `docs/functional-documents/verification-service-functional-document.md` (deleted 2026-08-06) |
| Source code | `src/services/verification/` |
| Config class | `verification.config.VerificationSettings` |
| DB schema | `verification` (owned by this service) |

---

## 2. Purpose and Scope

### 2.1 What this service does

The Verification Service scores predictions against actual market outcomes. It sits between the Prediction Service (which emits predictions) and the Credibility Service (which uses scores to update graph edge weights).

Specific responsibilities:

- **Evaluation creation** — on receiving a `PredictionMade` message, resolve the baseline and settlement sessions and emit a `PriceRequested` message so the Market Data Service fetches the required closes
- **Supersede handling** — if the prediction supersedes a prior prediction (market-closed collapse), mark the prior evaluation as WITHDRAWN so it is never scored
- **Price observation processing** — on receiving `PriceObserved`, validate the closes against the evaluation, compute the actual return, compare with the predicted direction, and emit a `PredictionScored` message
- **Score publication** — publish `PredictionScored` via the transactional outbox for the Credibility Service

### 2.2 What it does not do

- Does not update credibility weights or graph edges (Credibility Service)
- Does not fetch market prices (Market Data Service)
- Does not produce predictions (Prediction Service)
- Does not access Neo4j

---

## 3. Definitions

| Term | Meaning |
|---|---|
| Evaluation | A pending scoring task for one prediction; holds the sessions, direction, magnitude, and confidence from the prediction, plus the deterministic `request_id` for the price fetch |
| Baseline session | The most recent completed trading session at prediction time; the denominator of the actual-return formula |
| Settlement session | The next trading session after the baseline; the numerator of the actual-return formula |
| actual_return | `(settlement_close - baseline_close) / baseline_close`; a positive value means the price went up |
| Deadband | A return within `±0.003` (0.3%) is classified as NEUTRAL; outside is UP or DOWN |
| Magnitude thresholds | `\|actual_return\| < 0.01` → SMALL; `< 0.03` → MEDIUM; else LARGE |
| is_correct | `True` when `predicted_direction == actual_direction` (both must be the same label, including NEUTRAL) |
| score | 1.0 if correct, 0.0 if incorrect (binary) |
| request_id | `UUID5(_REQUEST_NAMESPACE, "{prediction_id}|{registry_version}")` — deterministic, unique per (prediction, registry version) |
| WITHDRAWN evaluation | A prediction that was superseded by a market-closed collapse; its evaluation exists but is never scored |
| resolve_baseline_settlement | Shared calendar function: given `decision_at` and the asset's timezone, returns `(baseline, settlement)` without look-ahead |

---

## 4. System Context

```
[Prediction Service]
       |
       | PredictionMade (routing key: prediction.made)
       | Queue: verification.predictions
       v
[Verification Service]
  - resolve baseline/settlement sessions
  - deterministic request_id
  - create evaluation + outbox row (PriceRequested)
  - handle supersede (withdraw prior)
       |
       | PriceRequested (routing key: price.requested)
       v
[Market Data Service]
       |
       | PriceObserved (routing key: price.observed)
       | Queue: verification.prices
       v
[Verification Service]
  - validate closes vs evaluation
  - compute actual_return / direction / magnitude
  - store score + outbox row (PredictionScored)
       |
       | PredictionScored (routing key: prediction.scored)
       v
[Credibility Service]
```

- Consumes from: `verification.predictions` (PredictionMade), `verification.prices` (PriceObserved)
- Publishes to: `feed.events` routing keys `price.requested` and `prediction.scored`
- No Neo4j access
- No LLM calls
- No external HTTP calls (all logic is local)

---

## 5. Functional Requirements

### 5.1 Prediction processing

| ID | Requirement | Status |
|---|---|---|
| VER-1 | The service shall consume `PredictionMade` messages from the `verification.predictions` queue | Implemented |
| VER-2 | On receiving a `PredictionMade`, the service shall resolve `(baseline_session, settlement_session)` using the asset's own timezone and session-completion time from the registry | Implemented |
| VER-3 | The `request_id` shall be computed deterministically as `UUID5(_REQUEST_NAMESPACE, "{prediction_id}|{registry_version}")` so that replaying the same prediction produces the same request | Implemented |
| VER-4 | The service shall create an `EvaluationRecord` and a `PriceRequested` outbox row in one atomic database transaction; if the `prediction_id` already exists in `evaluations`, the insert is skipped (`ON CONFLICT DO NOTHING`) | Implemented |
| VER-5 | If the incoming `PredictionMade` carries a non-null `supersedes_prediction_id`, the service shall mark the prior evaluation as WITHDRAWN so it is never scored | Implemented |
| VER-6 | An unknown asset ID in a `PredictionMade` message shall raise `InvalidPredictionError`; the message shall be dead-lettered | Implemented |
| VER-38 | A `PriceObserved` whose `request_id` has no evaluation row shall raise `OrphanedObservationError`, be **acknowledged** (not dead-lettered), logged at WARNING as `price_observed_orphaned` with the request, prediction and asset, and counted in `prices_orphaned` on `/health` | Implemented |

### 5.2 Session resolution

| ID | Requirement | Status |
|---|---|---|
| VER-7 | Baseline session = the most recent completed trading session at the time of `decision_at`, on the asset's own market calendar | Implemented |
| VER-8 | Settlement session = the next trading session after the baseline (ONE_TRADING_DAY horizon) | Implemented |
| VER-9 | "Complete" means `decision_at >= session_completed_at(session, timezone, hour, minute)` where `hour` and `minute` are per-asset registry fields | Implemented |
| VER-10 | If the current local session has not yet completed at `decision_at`, the baseline is the session before (no look-ahead) | Implemented |
| VER-11 | Session resolution uses the asset's `timezone` field from the registry; Stockholm and New York listings use their own clocks | Implemented |

### 5.3 Price observation processing

| ID | Requirement | Status |
|---|---|---|
| VER-12 | The service shall consume `PriceObserved` messages from the `verification.prices` queue | Implemented |
| VER-13 | On receiving a `PriceObserved`, the service shall look up the evaluation by `request_id`; if not found, raise `PriceValidationError` | Implemented |
| VER-14 | If the evaluation is in WITHDRAWN state, the service shall skip scoring and log `score_skipped_withdrawn` | Implemented |
| VER-15 | The service shall validate the observation against the evaluation before scoring (see 7.4 for the full validation list) | Implemented |
| VER-16 | After validation, the service shall store the raw `PriceObserved` in `price_observations` as an immutable audit record | Implemented |
| VER-17 | The service shall compute the score using the deterministic close-to-close formula (see 7.3) | Implemented |
| VER-18 | The service shall store the score row and the `PredictionScored` outbox row in one atomic transaction; if the `prediction_id` already exists in `scores`, the insert is skipped | Implemented |

### 5.4 Scoring formula

| ID | Requirement | Status |
|---|---|---|
| VER-19 | `actual_return = (settlement_close - baseline_close) / baseline_close` | Implemented |
| VER-20 | If `abs(actual_return) < deadband (0.003)`: `actual_direction = NEUTRAL` | Implemented |
| VER-21 | If `actual_return > 0` and outside deadband: `actual_direction = UP` | Implemented |
| VER-22 | If `actual_return < 0` and outside deadband: `actual_direction = DOWN` | Implemented |
| VER-23 | If `abs(actual_return) < magnitude_medium_min (0.01)`: `actual_magnitude = SMALL` | Implemented |
| VER-24 | If `abs(actual_return) < magnitude_large_min (0.03)`: `actual_magnitude = MEDIUM` | Implemented |
| VER-25 | If `abs(actual_return) >= magnitude_large_min (0.03)`: `actual_magnitude = LARGE` | Implemented |
| VER-26 | `is_correct = (predicted_direction == actual_direction)`; both must be the same label | Implemented |
| VER-27 | `score = 1.0` if `is_correct`, else `0.0` | Implemented |

### 5.5 Outbox relay

| ID | Requirement | Status |
|---|---|---|
| VER-28 | The service shall implement the transactional outbox pattern for both `PriceRequested` and `PredictionScored` messages | Implemented |
| VER-29 | The outbox table stores both message types; rows are distinguished by `message_type` field | Implemented |
| VER-30 | The scheduled job shall sweep `verification.outbox_events` for PENDING rows every `VERIFICATION_OUTBOX_INTERVAL_SECONDS` (default 30 s) and publish each to `feed.events` with the correct routing key | Implemented |
| VER-31 | A per-row publish failure shall increment `attempts` and write `last_error` without blocking other rows | Implemented |

### 5.6 Health and readiness

| ID | Requirement | Status |
|---|---|---|
| VER-32 | The service shall expose `GET /health` returning `{"status": "ok"}` | Implemented |
| VER-33 | The service shall expose `GET /ready` returning 200 only when the database pool and both RabbitMQ consumers are healthy | Implemented |

---

## 6. Non-Functional Requirements

| ID | Requirement | Status |
|---|---|---|
| VER-34 | Secrets (`DATABASE_URL`, `RABBITMQ_URL`) shall be environment variables; none committed | Implemented |
| VER-35 | The service shall bind to the local environment only | Implemented |
| VER-36 | The evaluation insert and outbox insert shall be atomic; a crashed consumer replays idempotently | Implemented |
| VER-37 | The scoring formula uses Python `Decimal` arithmetic for the close prices to avoid floating-point rounding errors in the return calculation | Implemented |

---

## 7. How It Works

### 7.1 PredictionMade processing pipeline

**Step 1 — Resolve asset and sessions**
```python
series = registry.resolve(message.asset_id)
baseline_session, settlement_session = resolve_baseline_settlement(
    message.decision_at,
    series.timezone,
    hour=series.session_completion_hour,
    minute=series.session_completion_minute,
)
```

**Step 2 — Compute deterministic request_id**
```python
_REQUEST_NAMESPACE = uuid5(NAMESPACE_URL, "feed.verification.price-request")
request_id = uuid5(_REQUEST_NAMESPACE, f"{prediction_id}|{registry_version}")
```

This is deterministic: replaying the same `PredictionMade` produces the same `request_id`. The `request_id` is also the idempotency key for the Market Data service.

**Step 3 — Create evaluation and outbox row**
- Build `EvaluationRecord` with prediction metadata + resolved sessions
- Build `PriceRequested` message
- Insert both in one transaction (`ON CONFLICT (prediction_id) DO NOTHING` on evaluations)
- Log `evaluation_created` or `evaluation_duplicate`

**Step 4 — Handle supersede**
- If `message.supersedes_prediction_id is not None`: mark the prior evaluation as WITHDRAWN
- A WITHDRAWN evaluation is skipped when `PriceObserved` arrives for it

### 7.2 resolve_baseline_settlement algorithm

```python
def resolve_baseline_settlement(decision_at, timezone_name, hour, minute):
    as_utc = decision_at.astimezone(UTC)
    local_date = as_utc.astimezone(local_tz).date()

    # Start from the local date; back up past weekends
    baseline = local_date
    while not is_trading_day(baseline):
        baseline -= timedelta(days=1)

    # No look-ahead: if today's session hasn't closed, use the previous completed session
    if not is_session_complete(baseline, timezone_name, now=as_utc, hour=hour, minute=minute):
        baseline = previous_session(baseline)

    settlement = next_session(baseline)
    return baseline, settlement
```

**Example (Stockholm listing, session close 17:30 local):**
- `decision_at = 2024-03-15T14:00Z` (15:00 Stockholm time on a Friday)
- Local date = 2024-03-15 (Friday = trading day)
- Session complete at 16:30 UTC (17:30 – 1h DST offset); `14:00 < 16:30` → not complete
- Baseline = `previous_session(2024-03-15)` = 2024-03-14 (Thursday)
- Settlement = `next_session(2024-03-14)` = 2024-03-15 (Friday)

**Example (New York listing, session close 17:00 ET):**
- `decision_at = 2024-03-15T23:30Z` (19:30 ET on a Friday)
- Local date = 2024-03-15 (Friday = trading day)
- Session complete at 22:00 UTC; `23:30 > 22:00` → complete
- Baseline = 2024-03-15 (Friday)
- Settlement = `next_session(2024-03-15)` = 2024-03-18 (Monday)

### 7.3 Scoring algorithm

```python
def score(baseline_close, settlement_close, predicted_direction, deadband, medium_min, large_min):
    actual_return = float((settlement_close - baseline_close) / baseline_close)
    magnitude_abs = abs(actual_return)

    # Direction
    if magnitude_abs < deadband:       actual_direction = NEUTRAL
    elif actual_return > 0:            actual_direction = UP
    else:                              actual_direction = DOWN

    # Magnitude
    if magnitude_abs < medium_min:     actual_magnitude = SMALL
    elif magnitude_abs < large_min:    actual_magnitude = MEDIUM
    else:                              actual_magnitude = LARGE

    is_correct = (predicted_direction == actual_direction)
    return ScoreOutcome(
        actual_return=actual_return,
        actual_direction=actual_direction,
        actual_magnitude=actual_magnitude,
        is_correct=is_correct,
        score=1.0 if is_correct else 0.0,
    )
```

**Default thresholds (from config):**

| actual_return | actual_direction | actual_magnitude |
|---|---|---|
| `\|r\| < 0.003` | NEUTRAL | SMALL |
| `0.003 ≤ \|r\| < 0.01`, r > 0 | UP | SMALL |
| `0.003 ≤ \|r\| < 0.01`, r < 0 | DOWN | SMALL |
| `0.01 ≤ \|r\| < 0.03` | UP or DOWN | MEDIUM |
| `\|r\| ≥ 0.03` | UP or DOWN | LARGE |

Note: `score = 1.0` when `predicted_direction == NEUTRAL AND actual_direction == NEUTRAL`. A NEUTRAL prediction can be correct if the market barely moved.

### 7.4 Observation validation (before scoring)

The service validates each `PriceObserved` against the stored evaluation:

| Check | What is verified |
|---|---|
| `message.asset_id == evaluation.asset_id` | No cross-asset confusion |
| `message.baseline.session == evaluation.baseline_session` | Correct baseline date |
| `message.settlement.session == evaluation.settlement_session` | Correct settlement date |
| `message.baseline.registry_version == evaluation.registry_version` | Asset mapping unchanged since the prediction |
| `message.settlement.registry_version == evaluation.registry_version` | Same for settlement |
| `message.baseline.price_kind == series.price_kind` | Correct close type |
| `message.settlement.price_kind == series.price_kind` | Same for settlement |
| `message.baseline.is_adjusted == series.is_adjusted` | No adjusted/unadjusted mismatch |
| `message.settlement.is_adjusted == series.is_adjusted` | Same for settlement |

Any mismatch raises `PriceValidationError`; the message is dead-lettered.

A **missing** evaluation is a different condition and is handled differently (VER-38). A mismatch means
the observation and its evaluation disagree — a contract violation, and the message is the evidence, so
it belongs in the dead-letter queue for inspection. An orphan means there is nothing to compare
against: no retry can succeed, and the evidence needed is the absent row, not the message. It is
therefore acknowledged and surfaced through a WARNING log and the `prices_orphaned` counter instead.

Both conditions previously raised `PriceValidationError`, so orphans were dead-lettered as poison. The
queue accumulated 14 such messages between 2026-08-13 and 2026-08-14 — every one an orphan, none a real
defect. That is the failure mode this split exists to prevent: a dead-letter queue full of unactionable
traffic is one an operator stops reading.

Orphans are expected in local testing, where an integration test deletes the predictions and
evaluations it created while a `PriceObserved` for them is still in flight. In production an orphan is
unexpected — nothing deletes an evaluation, and a superseded one becomes `WITHDRAWN` rather than
disappearing — which is why it is logged at WARNING rather than INFO.

### 7.5 Worked example

**Scenario:** AAPL prediction was UP for 2024-03-18. Baseline = 2024-03-15 (close $170.50), Settlement = 2024-03-18 (close $175.20).

1. `PredictionMade(asset_id=AAPL, direction=UP, magnitude=LARGE, confidence=0.85, decision_at=2024-03-15T21:00Z)` arrives
2. Session resolution: baseline = 2024-03-15 (complete at 22:00 UTC; 21:00 < 22:00 → not complete → use 2024-03-14)... Actually: 21:00 UTC = 17:00 ET (New York 17:00); but with the hour=17 default: `session_completed_at(2024-03-15, ET, hour=17) = 22:00 UTC`; `21:00 < 22:00` → baseline = 2024-03-14
   - Wait: for this example, say `decision_at = 2024-03-15T23:00Z` (18:00 ET) → session complete → baseline = 2024-03-15
3. `request_id = uuid5(namespace, "AAPL-uuid|v1.0")` — deterministic
4. `PriceRequested(request_id, prediction_id, asset_id=AAPL, baseline=2024-03-15, settlement=2024-03-18)` stored in outbox
5. Market Data service fetches both closes; emits `PriceObserved(baseline=$170.50, settlement=$175.20)`
6. Verification receives `PriceObserved`:
   - Evaluation found, not WITHDRAWN
   - Validation passes (sessions match, registry version matches)
   - `actual_return = (175.20 - 170.50) / 170.50 = 0.02756`
   - `|0.02756| ≥ 0.003` → not NEUTRAL
   - `0.02756 > 0` → UP
   - `0.01 ≤ 0.02756 < 0.03` → MEDIUM
   - `predicted=UP, actual=UP` → `is_correct=True`, `score=1.0`
7. `PredictionScored(prediction_id, asset_id=AAPL, predicted=UP/LARGE, actual=UP/MEDIUM, return=0.02756, correct=True, score=1.0)` stored and published

---

## 8. Interfaces

### 8.1 Consumed messages

| Queue | Message type | Purpose |
|---|---|---|
| `verification.predictions` | `PredictionMade` | Create evaluation, emit PriceRequested |
| `verification.prices` | `PriceObserved` | Validate closes, compute and emit PredictionScored |

### 8.2 Published messages

| Exchange | Routing key | Message type | When emitted |
|---|---|---|---|
| `feed.events` | `price.requested` | `PriceRequested` | On new evaluation creation |
| `feed.events` | `prediction.scored` | `PredictionScored` | After successful scoring |

See SRS-01 sections 5.7, 5.8, 5.9 for full field tables.

### 8.3 HTTP endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Returns `{"status": "ok"}` always |
| GET | `/ready` | Returns 200 if DB pool and both queue consumers are healthy |

### 8.4 Scheduled jobs

| Job | Interval | What it does |
|---|---|---|
| `outbox_sweep` | 30 s (configurable) | Publishes PENDING outbox rows (both PriceRequested and PredictionScored) |

---

## 9. Data Design

### 9.1 Table: `verification.evaluations`

| Column | Type | Notes |
|---|---|---|
| `prediction_id` | UUID PRIMARY KEY | From `PredictionMade` |
| `context_id` | UUID NOT NULL | The context that produced the prediction |
| `asset_id` | TEXT NOT NULL | Registry asset ID |
| `predicted_direction` | TEXT NOT NULL | `UP`, `DOWN`, or `NEUTRAL` |
| `predicted_magnitude` | TEXT NOT NULL | `SMALL`, `MEDIUM`, or `LARGE` |
| `confidence` | DOUBLE PRECISION NOT NULL | From the prediction |
| `decision_at` | TIMESTAMPTZ NOT NULL | When the prediction was made |
| `baseline_session` | DATE NOT NULL | Resolved baseline session date |
| `settlement_session` | DATE NOT NULL | Resolved settlement session date |
| `market_calendar` | TEXT NOT NULL | Asset timezone (e.g. `America/New_York`) |
| `registry_version` | TEXT NOT NULL | Asset registry version at evaluation creation |
| `request_id` | UUID NOT NULL UNIQUE | Deterministic price-fetch request ID |
| `correlation_id` | UUID NOT NULL | Propagated from the prediction |
| `contributing_edges` | JSONB DEFAULT '[]' | Copied from `PredictionMade` for the score message |
| `source_ids` | JSONB DEFAULT '[]' | (Currently empty; reserved for future use) |
| `status` | TEXT DEFAULT 'PENDING' | `PENDING` or `WITHDRAWN` |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| `updated_at` | TIMESTAMPTZ DEFAULT now() | Updated when status changes to WITHDRAWN |

Index: `evaluations_status_idx ON (status)` — used by the withdraw query.

### 9.2 Table: `verification.price_observations`

| Column | Type | Notes |
|---|---|---|
| `request_id` | UUID PRIMARY KEY | Idempotency key; matches the evaluation |
| `prediction_id` | UUID NOT NULL | |
| `asset_id` | TEXT NOT NULL | |
| `baseline` | JSONB NOT NULL | Full `CloseObservation` for the baseline |
| `settlement` | JSONB NOT NULL | Full `CloseObservation` for the settlement |
| `received_at` | TIMESTAMPTZ DEFAULT now() | When the PriceObserved message arrived |

Stored as an immutable audit record; scoring uses the values directly from the `PriceObserved` message, not a re-read of this table.

### 9.3 Table: `verification.scores`

| Column | Type | Notes |
|---|---|---|
| `prediction_id` | UUID PRIMARY KEY | Idempotency key: one score per prediction |
| `asset_id` | TEXT NOT NULL | |
| `predicted_direction` | TEXT NOT NULL | |
| `actual_direction` | TEXT NOT NULL | |
| `predicted_magnitude` | TEXT NOT NULL | |
| `actual_magnitude` | TEXT NOT NULL | |
| `confidence` | DOUBLE PRECISION NOT NULL | |
| `actual_return` | DOUBLE PRECISION NOT NULL | `(settlement - baseline) / baseline` |
| `is_correct` | BOOLEAN NOT NULL | `predicted_direction == actual_direction` |
| `score` | DOUBLE PRECISION NOT NULL | `1.0` or `0.0` |
| `scored_at` | TIMESTAMPTZ NOT NULL | Timestamp of scoring |

### 9.4 Table: `verification.outbox_events`

| Column | Type | Notes |
|---|---|---|
| `message_id` | UUID PRIMARY KEY | Idempotency key |
| `aggregate_id` | UUID NOT NULL | `prediction_id` or `request_id` |
| `message_type` | TEXT NOT NULL | `"PriceRequested"` or `"PredictionScored"` |
| `payload` | TEXT NOT NULL | JSON-serialised message |
| `delivery_status` | TEXT DEFAULT 'PENDING' | `PENDING` or `DELIVERED` |
| `attempts` | INTEGER DEFAULT 0 | |
| `last_error` | TEXT | |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |
| `delivered_at` | TIMESTAMPTZ | |

Note: `payload` is stored as `TEXT` (not `JSONB`). The `message_type` field is used by the outbox sweeper to determine which routing key to use when publishing.

---

## 10. Configuration

All variables use the `VERIFICATION_` prefix unless noted.

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | _(required)_ | PostgreSQL connection string (no prefix) |
| `RABBITMQ_URL` | _(required)_ | RabbitMQ connection string (no prefix) |
| `LOG_LEVEL` | `INFO` | Logging verbosity (no prefix) |
| `VERIFICATION_PREDICTIONS_QUEUE` | `verification.predictions` | Queue for PredictionMade messages |
| `VERIFICATION_PRICES_QUEUE` | `verification.prices` | Queue for PriceObserved messages |
| `VERIFICATION_DEADBAND` | `0.003` | Returns within ±0.3% classified as NEUTRAL |
| `VERIFICATION_MAGNITUDE_MEDIUM_MIN` | `0.01` | 1% return threshold for MEDIUM magnitude |
| `VERIFICATION_MAGNITUDE_LARGE_MIN` | `0.03` | 3% return threshold for LARGE magnitude |
| `VERIFICATION_OUTBOX_INTERVAL_SECONDS` | `30` | How often the outbox sweep job runs |
| `VERIFICATION_DB_POOL_MIN_SIZE` | `1` | asyncpg minimum pool connections |
| `VERIFICATION_DB_POOL_MAX_SIZE` | `5` | asyncpg maximum pool connections |

---

## 11. Verification

| Requirement | Test file | What is verified |
|---|---|---|
| VER-7 – VER-11 (session resolution) | `tests/test_sessions.py` (shared calendar) | Baseline before close time uses prior session; timezone-correct close times |
| VER-19 – VER-27 (scoring formula) | `tests/test_scoring.py` | Deadband boundary; UP/DOWN classification; magnitude thresholds; NEUTRAL correct/incorrect |
| VER-1 – VER-6 (prediction processing) | `tests/test_pipeline.py` | Evaluation creation; idempotency; supersede withdraw; unknown asset error |
| VER-12 – VER-18 (price processing) | `tests/test_pipeline.py` | Observation validation; WITHDRAWN skip; score row; outbox row |
| VER-15 (observation validation) | `tests/test_pipeline.py` | All 8 validation checks; each mismatch raises PriceValidationError |
| VER-28 – VER-31 (outbox relay) | `tests/test_pipeline.py` | Both message types published; per-row failure isolation |
| End-to-end | `tests/test_integration.py` | Full PredictionMade → PriceRequested → PriceObserved → PredictionScored flow |

---

## 12. Failure Handling

| Failure scenario | Behaviour |
|---|---|
| Unknown asset in `PredictionMade` | `InvalidPredictionError`; message dead-lettered |
| Duplicate `PredictionMade` (same prediction_id) | `ON CONFLICT DO NOTHING`; silently idempotent; `evaluation_duplicate` log |
| Superseded prediction (supersedes_prediction_id set) | Prior evaluation marked WITHDRAWN; it will never be scored |
| `PriceObserved` for unknown request_id | `OrphanedObservationError`; message **acknowledged**, logged WARNING `price_observed_orphaned`, counted in `/health.prices_orphaned` (VER-38) |
| `PriceObserved` for WITHDRAWN evaluation | Skipped with log `score_skipped_withdrawn`; no score emitted |
| Observation validation failure (session/version/kind mismatch) | `PriceValidationError`; message dead-lettered |
| Duplicate `PriceObserved` (score already exists) | `ON CONFLICT DO NOTHING`; `score_duplicate` log; no second score emitted |
| Outbox publish failure | `attempts` incremented, `last_error` written; retried next sweep |
| Database connection lost mid-processing | Transaction rolls back; evaluation stays PENDING; re-processed on message replay |

---

## 13. Assumptions and Limitations

### 13.1 Accepted design decisions

| Decision | Rationale |
|---|---|
| Deterministic request_id (UUID5) | If the Verification Service crashes after creating the evaluation but before the Market Data service registers the request, the message replay produces the same request_id, so Market Data's idempotency guard handles the duplicate correctly |
| Binary score (1.0 / 0.0) | Keeps the Credibility Service's edge-weight update simple; a partial credit scheme is a future enhancement |
| NEUTRAL prediction can be correct | The Prediction Service emits NEUTRAL when evidence is weak (PRD-24); it is correct when the market barely moved, which is roughly a quarter of outcomes. The Credibility Service handles this appropriately |
| close-to-close return | Uses two settled official closes; does not use intraday or mid-session prices; avoids noise from bid/ask spread |
| No magnitude in correctness check | A LARGE UP prediction is correct if the actual direction is UP, regardless of whether the magnitude was SMALL or LARGE; magnitude is recorded but not part of the binary score |
| Superseded evaluation WITHDRAWN immediately | The prior prediction never had its own settlement session (it was replaced before the market opened); scoring it with the new settlement close would be meaningless |

### 13.2 Known limitations

- **NEUTRAL predictions are rarely correct** — when actual_return is between ±0.003, actual_direction is NEUTRAL. Predictions with direction UP or DOWN are incorrect on these small-move days. Given the Prediction Service rarely emits NEUTRAL (see deadband discussion in SRS-04), most predictions are scored against UP or DOWN actuals.
- **Score is binary** — directional accuracy only; magnitude accuracy is recorded but not used in the Credibility Service's current update formula.
- **source_ids always empty** — the `source_ids` field in the evaluation is populated as `[]` in the current code; it was intended to carry article/event source IDs for downstream attribution but is not yet populated.
- **No timezone for session_completion_hour/minute in service config** — these values come from the asset registry, which means adding a new market with a non-standard close time requires a registry edit, not a service config change. This is intentional but means the service has a registry runtime dependency.

---

## 14. How to Update This Document

### 14.1 When to update

Update this document whenever any of the following changes:

- A new field is added to `PredictionMade`, `PriceObserved`, or `PredictionScored`
- The session resolution logic changes (e.g. holiday modelling is added)
- The scoring formula changes (deadband, magnitude thresholds, or scoring metric)
- The supersede / withdraw logic changes
- The observation validation checks change
- An environment variable is added, removed, or has its default changed in `config.py`
- A new table column is added or modified in `db.py`
- A new test file is added (add it to section 11)

### 14.2 Steps to update

1. **Read the current source first** — verify behaviour before writing requirements
2. **Assign the next VER-N ID** — check the highest existing ID and continue the sequence
3. **Update the relevant section**
4. **Add a row to section 15** (Change History) with date, what changed, and why
5. **Do not renumber existing IDs** — mark removed requirements as `Status: Withdrawn`
6. **Update `requirements/README.md`** if the ID range for VER changes

---

## 15. Change History

| Date | Description |
|---|---|
| 2026-08-05 | Initial as-built specification for E06 (Verification Service); VER-1 through VER-37 |
| 2026-08-14 | **Defect fix.** `PriceObserved` for a missing evaluation and `PriceObserved` that contradicts its evaluation both raised `PriceValidationError`, so both were dead-lettered as poison. Orphans can never be resolved by retry or by inspecting the message, so the `verification.prices.dlq` queue filled with unactionable traffic (14 messages, all orphans, zero real defects) while genuine mismatches would have been indistinguishable in it. Split into `OrphanedObservationError`, which is acknowledged, logged at WARNING as `price_observed_orphaned`, and counted in `/health.prices_orphaned`. `PriceValidationError` still dead-letters. VER-38 added; §12 and the scoring section updated |
