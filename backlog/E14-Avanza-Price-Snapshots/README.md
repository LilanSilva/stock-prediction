# E14 — Avanza live price snapshots

Status (2026-09-25): **Functionality implemented — Done. Live validation — In progress.**
The Market Data API, Avanza worker and Verification sampled support are deployed with the current
code. Sampled verification remains OFF. Pilot acceptance is not complete.

## Completed functionality

The statuses below describe implemented functionality, not completion of the live rollout.

| Story | Implementation status | Owning specification |
|---|---|---|
| S01: Initial unattended-access investigation | **Done** — Windows Chrome and Linux browser probes completed; sustained access remains a live-validation gate | [SRS-05 limitations](../../requirements/SRS-05-market-data.md#13-assumptions-and-limitations) |
| S02: Mapping and discovery | **Done** — 29 reviewed mappings, deterministic discovery and version validation | [REF-02 companion mappings](../../requirements/REF-02-asset-registry.md#avanza-companion-mappings) |
| S03: Durable storage and jobs | **Done** — scheduled jobs, immutable samples, leases, retries and transactional outbox | [SRS-05 data design](../../requirements/SRS-05-market-data.md#9-data-design) |
| S04: Browser collection and read APIs | **Done** — currency/identity checks, market-calendar scheduling, cache bypass, diagnostics and additive endpoints | [SRS-05 worker](../../requirements/SRS-05-market-data.md#avanza-snapshot-worker) |
| S05: Shadow sample consumption | **Done** — separate event, queues/DLQs, OFF/SHADOW policy and diagnostic endpoint; deployed with mode OFF | [SRS-06 sampled flow](../../requirements/SRS-06-verification.md#sampled-shadow-flow) |

## Deployment status

| Component | Status | Latest verified evidence |
|---|---|---|
| Market Data API | **Done** | Deployed snapshot API/storage and shared contract match current source; `/ready` passes; snapshot routes restored |
| Avanza snapshot worker | **Done** | Deployed reader, scheduler, storage, mappings loader, discovery, cache bypass and logging match current source |
| Verification sampled support | **Done** | Rebuilt and deployed using `infra/.env`; sampled modules, topology and shared contract match current source; readiness passes and sampled endpoint returns the expected HTTP 503 OFF response |

Verification deployment was completed at 21:51 UTC on September 25. The sampled endpoint now returns
`{"mode":"OFF"}` with HTTP 503 instead of route-not-found (HTTP 404). Keep sampled verification OFF
unless a separate SHADOW validation is explicitly enabled. Generic service health does not prove
optional functionality was deployed; verify installed modules and feature endpoints as well.

## Verification and testing status

| Check | Status | Evidence or remaining work |
|---|---|---|
| Automated regression, contract, lint and type checks | **Done for implemented changes** | Recorded passing suites and known unrelated tooling/type limitations are listed below |
| Real PostgreSQL/RabbitMQ isolation, replay and idempotency checks | **Done** | Disposable-infrastructure tests passed; see recorded validation below |
| Browser-cache regression | **Done** | Cached fixture reproduced stale reads; both temporary and persistent profiles retrieved updated values with the safeguard |
| One-time provider access and currency checks | **Done** | Linux reads succeeded for all 29 mappings across four currencies; these were diagnostic reads |
| Scheduled collection through storage and publication | **In progress** | Five real Tesla jobs stored/published; no scheduled samples yet for Saab B, Novo Nordisk B or SAP |
| S06: Three complete trading sessions across the four pilot companies | **In progress** | Friday September 25 was partial and does not count; monitoring planned for September 28–October 2 |
| Regular opening, closing, holiday/weekend and no-after-close behavior | **In progress** | Calendar tests passed and Tesla closing jobs ran; complete live transitions across all pilot exchanges remain |
| Avanza page market-state recognition at close | **Needs completion** | Tesla close checks stored UNKNOWN; inspect the observed status text and confirm or improve recognition without guessing from color |
| Earlier execution gap and missed reads | **Needs completion** | 131 missed slots and two deadline failures recorded; root cause remains unconfirmed despite added timing logs |
| Sustained recovery and capacity acceptance | **Needs completion** | Measure read success/latency/resources and recovery under downtime, broker outage and mapping repair; do not expand beyond the pilot yet |
| Sampled Verification deployment and OFF-mode runtime checks | **Done** | Current modules verified, service healthy/ready, documented OFF response confirmed, and existing Market Data API still available |
| Live sampled Verification validation | **Needs completion** | Separate from deployment and automated integration tests; any controlled SHADOW run must be explicitly enabled, and delayed unknown-time observations remain ineligible |

Acceptance evidence must distinguish successful collection from freshness eligibility. Existing
daily/minute APIs and live scoring remain unchanged; switching their data source is not part of
the completed work. No valid sampled scores or confirmed official closes have been demonstrated.

## Remaining pilot

Completion cleanup requested by the user: once the entire plan, including the pilot and rollout,
is complete, remove the E14 entry from [the backlog README](../README.md). Keep it listed while
these items remain pending; implementation alone does not satisfy this completion condition.

Run at least three trading sessions on 2–4 reviewed listings after deployment access is confirmed.
Measure scheduled-read success independently from freshness eligibility; exercise downtime,
late jobs, market open/close, broker outage, mapping repair and capacity at the intended concurrency.
Promote toward ten tabs only after the measured latency/resource budget passes. Unknown provider
quote time remains ineligible for strict verification, so this pilot can produce an observational
dataset without producing valid sampled scores. Official close promotion is not implemented.

Do not represent completed deterministic or one-time browser tests as a completed trading-session
pilot. Deployment/rollback and operator recovery commands live in
[SRS-05](../../requirements/SRS-05-market-data.md#snapshot-deployment-and-recovery).

## Implementation validation

### Verification deployment completion — 2026-09-25

Built `feed-analyzer/verification:0.1` from the current committed implementation using Docker Desktop,
`infra/.env` and the workstation's trusted-CA base image. Compose configuration validation passed.
All 40 Verification unit tests passed against the new image; optional integration tests were not
rerun for this deployment. Recreated only `feed-verification` with `--no-deps --no-build`.

The replacement started at 21:51:26 UTC with image digest
`sha256:0a6b478cbf5708a6b56845b87eb518c5f8696569ca7c1538302c89035d3a37be`.
Normalized source hashes matched for Verification app/config, sampled collector/policy, sample
topology and shared messages. Docker health and HTTP readiness passed. Both sampled and intraday
verification modes remain OFF; `/verification/sampled/{prediction_id}` returns HTTP 503 with
`{"mode":"OFF"}`, confirming the route is installed rather than absent.

Market Data and the Avanza worker retained their previous images/start times, and PostgreSQL,
RabbitMQ and Neo4j were not restarted. Both snapshot endpoints remained available with five stored
Tesla samples, a current collector heartbeat and zero pending outbox events. `/prices/recent`
returned HTTP 200. This completes deployment verification, not the multi-session live pilot.

### Active pilot observations — 2026-09-25

Monitoring found collection disabled in `infra/.env` and all mappings disabled. Collection is now
enabled with mapping version `avanza-v2-pilot-20260925`: SAAB_B_STO (SEK), NOVO_B_CPH (DKK),
SAP_ETR (EUR) and TSLA_NASDAQ (USD), at concurrency two and a 15-minute cadence. The other 25
listings remain disabled. Sample verification remains OFF.

The 19:30 UTC Tesla job succeeded on its first attempt: 372.15 USD observed at 19:30:03.584195 UTC,
stored under sample ID `03e630fd-3b57-5049-8e5d-d5f555153250`, returned by `/snapshots/recent` and
published through the outbox with no pending events. A separate diagnostic read succeeded for all
four listings with matching currencies; European markets correctly reported REGULAR_CLOSED.
Those European diagnostic reads were not persisted as regular-session samples. Avanza still
reports a 900-second delay without an exact quote timestamp, so samples remain FRESHNESS_UNKNOWN.

Two jobs scheduled at 12:30 UTC finished near 19:29 and failed their deadlines. Host and container
clocks agreed when checked afterward, and the worker had not restarted. The long execution gap's
cause is unconfirmed; suspension or a clock jump are possibilities, not established findings.
At 19:35 UTC there were 131 missed slots, two failed jobs and one successful job. Missed slots are
not fabricated or backfilled. Added per-job timing/error logs support investigation if it recurs.
The diagnostic update passed 22 snapshot tests, Ruff and strict typing, then was rebuilt and
deployed to the snapshot worker alone. At 19:39 UTC its heartbeat was current, its status RUNNING,
and its outbox empty; both Market Data and Verification readiness checks passed.

A follow-up cache regression reproduced stale cache reuse across ordinary tabs. The safeguarded
reader retrieved updated server prices for both temporary and persistent profiles. Validation passed
24 snapshot tests (including two real Chromium cache cases), 210 shared tests, Ruff and strict typing.
The cache policy is specified in [SRS-05](../../requirements/SRS-05-market-data.md#avanza-snapshot-worker).
The rebuilt worker was deployed using `infra/.env`; a diagnostic Avanza read from that container
returned Tesla at 371.48 USD at 19:58:45 UTC with REGULAR_OPEN status. This diagnostic was not
inserted into the scheduled sample stream.

The requested monitoring window is Monday September 28 at 09:00 through Friday October 2, 2026,
Europe/Berlin local time, with 15-minute checks during the relevant market sessions and Tesla sample
reports. Weekend monitoring is not required. Three complete trading sessions, including regular
European collection and open/close transitions, still need observation; this partial activation day
does not satisfy that gate. The host and Docker Desktop must remain running for collection.

### Close checks and API restoration — 2026-09-25

The 20:23 UTC monitor confirmed five successful Tesla jobs: regular slots at 19:30 and
19:45 UTC, followed by close checks at 20:00, 20:02 and 20:05 UTC. All succeeded on their
first attempt, with USD currency, no duplicate slots, and all five outbox events delivered
without retries. The sampled-verification queue holds those five messages with no consumer
because strict sampled verification remains OFF; its DLQ is empty. The 131 missed slots and
two earlier deadline failures are unchanged and are not new closed-market fetch failures.
The calendar closed Tesla's session at 20:00 UTC (22:00 CEST); its next regular session opens
2026-09-28 at 13:30 UTC (15:30 CEST). The final close observation remains CLOSE_UNCONFIRMED
and FRESHNESS_UNKNOWN, with UNKNOWN page market state and no provider quote timestamp.
This partial activation session does not count toward the three complete sessions.

The monitor also found `/snapshots/status` and `/snapshots/recent` returning HTTP 404.
The Market Data API container had been replaced at 20:16 UTC with an image lacking the
`market_data.snapshots` package, while the separate snapshot worker continued normally.
Rebuilt the API from committed revision `7868bfa` and redeployed only `feed-market-data`
using `infra/.env`. The replacement passed 84 API/non-browser unit tests, including snapshot
route and legacy API compatibility tests. Browser-worker tests require the optional Playwright
dependency and were excluded from this API-only test run; the worker was not rebuilt or restarted.
At 20:28 UTC, both snapshot endpoints returned successfully, the recent-samples API returned
all five stored observations, `/ready` passed, and `/prices/recent` returned HTTP 200.
The collector heartbeat remained current and its outbox remained empty. No samples were
inserted, replayed or changed during recovery. Deployment checks should verify these endpoints
in addition to generic health, which had remained green on the image missing snapshot support.

### Initial deployment and regression checks

Deployment on 2026-09-25 used `infra/.env` and rebuilt Market Data, Verification and the snapshot
worker with the workstation's trusted-CA base image. Both APIs passed Docker health and HTTP readiness
checks. At initial deployment the snapshot worker exited successfully with collection disabled and
reported NOT_STARTED; the pilot was activated afterward as recorded above. The checked recent-close
response and the database/broker/Neo4j/seed container identities and start times were unchanged.

The final Linux regression run passed 106 Market Data, 40 Verification and 210 shared unit tests.
Four additional tests passed against disposable PostgreSQL/RabbitMQ: immutable sample/outbox replay,
mapping changes and lease fencing, sampled finalization/withdrawal, and real message/DLQ delivery.
Compose validation and the snapshot image build passed. A TLS-verified Linux probe read all 29
configured listings across four currencies. These are one-time checks, not the trading-session pilot.

Windows Application Control blocked pandas/mypy DLLs during the final host rerun; the complete unit
suite was run successfully in the intended Linux runtime. A broader pre-existing Market Data type
check reports two `str` versus `AssetId` errors in `adapters/router.py:81`; this change does not alter
that router. New snapshot production modules pass strict type checking.
