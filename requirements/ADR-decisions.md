# ADR: Architecture Decision Records

## 1. Document control

| | |
|---|---|
| Document ID | `ADR` |
| Type | Decision record |
| Status | `Implemented` (ADR-001 … ADR-011 all Accepted) |
| Version | `1.4.0` |
| Diagrams | [docs/architectural-documents/](../docs/architectural-documents/) |
| Last verified against code | `2026-08-12` |

This document records **why** the system is shaped the way it is. It contains no `shall` statements —
the binding requirements live in [SyRS-system.md](SyRS-system.md) and the SRS documents, which state
*what* the system does. An ADR carries the reasoning a requirement alone cannot.

Only lightweight ADRs are maintained, for decisions that materially shape the POC. Formal
`FR-*`/`NFR-*`/`BR-*` identifier traceability is intentionally not used; the requirement IDs in the
SyRS and SRS documents supersede it.

An ADR is **never rewritten to match a later decision.** A superseded ADR keeps its text and gains a
`Superseded by` note, so the reasoning trail survives.

## 2. Decision index

| ADR | Decision | Status | Binding requirements |
|---|---|---|---|
| ADR-001 | Multi-event context before prediction | Accepted | [`SYS-3`](SyRS-system.md#51-product-behaviour), [SRS-04](SRS-04-prediction.md) |
| ADR-002 | One PostgreSQL database with service-owned schemas | Accepted | [`SYS-15` … `SYS-19`](SyRS-system.md#53-data-ownership) |
| ADR-003 | Topic exchange with one queue per consumer | Accepted | [`SYS-20` … `SYS-22`](SyRS-system.md#54-messaging) |
| ADR-004 | Verification owns evaluation and one dual-session price request | Accepted | [SRS-06](SRS-06-verification.md), [SRS-05](SRS-05-market-data.md) |
| ADR-005 | Token-efficient conditional LLM use | Accepted | [`SYS-29` … `SYS-38`](SyRS-system.md#55-llm-usage-policy) |
| ADR-006 | Conditional causal graph with event polarity and offline structure learning | Accepted | [SyRS §9.2](SyRS-system.md#92-neo4j-graph-model), [SRS-07](SRS-07-credibility.md) |
| ADR-007 | Multi-market coverage via a file-driven asset registry | Accepted | [`SYS-9` … `SYS-14`](SyRS-system.md#52-identity-and-reference-data), [REF-02](REF-02-asset-registry.md) |
| ADR-008 | Cross-asset `CORRELATES_WITH` propagation with visited-set cycle guard | Accepted | [SRS-04](SRS-04-prediction.md), [SRS-07](SRS-07-credibility.md) |
| ADR-009 | Separate intraday target-hit evidence from daily close-to-close learning | Accepted | [SRS-06](SRS-06-verification.md), [SRS-05](SRS-05-market-data.md) |
| ADR-010 | Isolate prospective point samples and sampled verification | Accepted | [SRS-05](SRS-05-market-data.md), [SRS-06](SRS-06-verification.md) |
| ADR-011 | Capture research evidence independently of official KG publication | Accepted | PRD-64–PRD-69 in [SRS-04](SRS-04-prediction.md#712-research-evidence-capture) |

## ADR-001: Multi-event context before prediction

Prediction groups distinct events by canonical asset and event-time window. One immutable prediction
is produced per asset/context version. Late events may create a superseding version.

## ADR-002: One database, schema ownership

The POC uses one PostgreSQL database to permit read-only BFF joins while preserving ownership through
schemas and roles. Database-per-service may be reconsidered only when independent deployment needs
justify it.

## ADR-003: Event exchange and consumer queues

Producers publish to `feed.events`. Each business consumer and live-update observer uses its own bound
queue. No observer consumes another service's work queue.

## ADR-004: Verification owns evaluation

Prediction publishes the forecast. Verification resolves baseline and settlement sessions and
publishes one request containing both. Market Data returns both approved immutable reference closes
with explicit price kind and provider metadata.

## ADR-005: Conditional LLM use

Local deterministic processing is the default. For M1, LLM use is restricted to ambiguous
cleansing/factual-conflict resolution. The shared LLM gateway is provider-configurable through
environment settings and API keys, so the project can switch models/providers without changing service
business logic. Prediction-time LLM arbitration remains deferred after the POC-6 `STOP` result unless
a new controlled hypothesis is approved.

## ADR-006: Conditional causal graph with polarity and offline learning

A bare `factor -> asset` edge cannot express context, so it wrongly treated every military conflict
as oil-positive and could not represent de-escalation. The graph now stores the condition as a
property on the `CAUSES` edge (each `factor, asset, condition` is a distinct edge), events carry a
`polarity` (`OCCURRENCE`/`RESOLUTION`) that flips the edge sign at decision time, and a
price-derived `RISK_PREMIUM_ELEVATED` condition gates resolution-driven reversals so a de-escalation
only predicts a drop when there is an elevated premium to unwind. Decisions stay graph-only with no
prediction-time LLM calls (POC-6 `STOP` preserved). Edge reliability is refined online by Credibility
per scored prediction and offline by a deterministic structure learner that mines historical events
against realized price moves; the initial graph is still an idempotent expert seed, and the learner
only refines or adds edges (non-destructive `MERGE`).

**Amended 2026-08-14 (E12 S02).** Conditioned edges are attached to `:AssetGroup` nodes, not to the
commodity `:Asset` nodes the design originally named. `infra/neo4j/init/04` and all of `05` targeted
`(:Asset {id: 'GOLD'})` and `BRENT_OIL`, which `02-seed-assets.cypher` never creates — the registry had
migrated to equity proxies (`NEM_NYSE`, `XOM_NYSE`) because Market Data can price an equity and
Verification can score it. Cypher's `MATCH ... MERGE` is a silent no-op when the `MATCH` binds nothing
and `cypher-shell` still exits 0, so every prior in those two files was discarded at seed time, with no
error, for as long as they existed. `09-verify-seed.cypher` now aborts the seed on that class of fault.

Two further constraints were established while fixing it, both verified against the live graph:

- **A `(factor, target)` pair is declared in exactly one seed file.** `MERGE (cf)-[r:CAUSES]->(g)` with
  no properties matches ANY existing `CAUSES` relationship between the two nodes, including a
  conditioned one — so an unconditional statement running after a conditioned one does not create a
  second edge, it silently rebinds and overwrites the conditioned edge's weight. `05` owns every
  conditioned edge; `06` owns every unconditional one.
- **A target carries a conditioned edge only where the condition changes the outcome**, meaning only
  where one condition implies no edge at all. Condition tags are unioned across the events in a context,
  so both `SAFE_HAVEN_ONLY` and `TRANSPORT_AFFECTED` can be active at once; a target holding one edge
  per condition would then fire both and count the factor twice. Oil qualifies (a distant conflict
  leaves it untouched); gold does not (it rises either way), so gold's prior stays unconditional.

The gating itself was already working for the case it was designed for: a `SAFE_HAVEN_ONLY` conflict
fires no oil edge, because `06` conditions the `OIL_GAS` edge independently of the dead files.

## ADR-007: Multi-market coverage via a file-driven asset registry

Every tradeable asset was priced by a US bellwether, so Swedish news about SAAB or Volvo could not
drive a Swedish-listed prediction, and the asset list itself was a closed Python enum — adding a
company meant a code change and a redeploy. Four decisions follow.

**Companies are assets; industry is a group.** One asset must map to exactly one price series, or a
prediction cannot be scored: `LMT` (USD), `SAAB-B` (SEK) and `AM` (EUR) are three companies, prices
and currencies. Multi-ticker assets were rejected for that reason. Instead each listing is its own
asset and an `AssetGroup` is a fan-out target, never itself tradeable. This also gives news a scope: a
company keyword moves that company alone ("Tesla acquired"), an industry keyword moves every member
across markets ("war begins"), and the company match always wins as the more specific signal.

**The registry is data, not code.** `AssetId` became a registry-validated string loaded from
`assets.json` (`ASSET_REGISTRY_PATH` in deployment) rather than a `StrEnum`, so a market is added by
editing a mounted file. Asset ids were already `TEXT` in Postgres and `Asset.id` in Neo4j, so no
migration was required. Unknown ids are still rejected at construction and at every message boundary;
the loader refuses to boot on a malformed file rather than falling back silently, because believing an
edit took effect while running on stale data is worse than failing loudly. The cost accepted: mypy can
no longer prove exhaustive coverage over assets.

**Providers are per asset.** No vendor covers every market, so the registry's `provider` field routes
each asset to its adapter. This also corrects the record: the `HTTP 429`s that retired Yahoo in POC-7
were caused by POC-6's custom User-Agent, not IP rate limiting (40 concurrent requests with a browser
agent all return 200; 8 with curl's default agent all return 429).

**Every asset now routes to Yahoo (2026-08-29), reversing the POC-7 choice of biquote.** biquote was
adopted as a documented API with no anti-scraping measures, but it is a quote/CFD feed rather than
consolidated exchange trades — its bars carry `volume: 0` with only a `tickVolume`, and its prices sit
on half-cents (`598.955`), the signature of a bid/ask midpoint. Two consequences made it unusable as a
reference-price source:

- **It omits roughly 40% of trading sessions.** Measured 2026-08-29 over one 20-day window, biquote
  returned 8–9 bars where Yahoo returned 15; for `XOM` it had no bar for 17, 18, 19, 21, 24, 25 or 28
  August, all ordinary weekday sessions. Because a missing bar raises `PriceNotYetAvailableError`,
  which the scheduler treats as "not ready yet", 12 price requests retried indefinitely — one reaching
  47 attempts over 12 days — and their predictions were never scored.
- **Its closes disagree with the exchange.** Where both vendors had the same session, they differed by
  up to `0.702%` (`LMT` 2026-08-10: biquote `598.955` vs Yahoo `603.160`). Any per-session fallback
  therefore mixes two price bases inside a single close-to-close return.

Yahoo was verified to serve all 10 affected assets with complete sessions and passing identity
validation, and it already priced US listings (`NEM_NYSE`, `UAL_NASDAQ`) in production. The router's
per-asset `provider` dispatch is retained, so re-introducing a second vendor remains one field in the
JSON. Accepted cost: Yahoo's endpoint is undocumented and its UA-sniffing may change without notice,
which is now a single point of failure for price data.

**Session calendars are per market.** `shared.calendar` resolves any IANA timezone through stdlib
`zoneinfo` instead of hand-rolled US DST arithmetic, because the EU and US switch on different dates
(2026-03-10: New York already `UTC-4`, Stockholm still `UTC+1`) and one hardcoded rule cannot serve
both. Each asset's closing clock comes from the registry: Stockholm completes at 18:00 local, five
hours before New York. Holidays remain unmodelled — a session is any weekday and a local holiday
surfaces as a missing provider bar, exactly as US holidays already behave.

**Edge inheritance.** Causal edges may attach to an asset or to its group. An asset's own edge always
wins; otherwise it inherits its group's, flagged with `inherited_from`. Without this a newly listed
company would produce no predictions until the offline learner accumulated enough company-specific
samples (5+ per factor/condition within a 30-day window), which thin listings might never reach. An
inherited edge's `edge_id` names the group edge, so Credibility updates the industry prior that
actually fired rather than inventing a per-asset edge that was never seeded.

Group priors are seeded as expert edges (`infra/neo4j/init/06-seed-group-edges.cypher`) with weights
deliberately below the commodity edges: a macro factor moves a whole industry less reliably than it
moves gold, and these carry no evidence yet, so starting modest lets real outcomes pull them up rather
than having to walk them down. `CORPORATE_EARNINGS` gets a prior on every group, because
company-specific news is the case this feature exists for and that factor previously had no edge at
all. Inheritance without a seeded prior is inert — the mechanism resolves to nothing — so the two
belong together.

## ADR-008: Cross-asset `CORRELATES_WITH` propagation with visited-set cycle guard

The existing `(:CausalFactor)-[:CAUSES]->(:Asset)` graph cannot model second-order price causation —
a directional move in one asset that reliably causes a directional move in another. The
capital-rotation scenario (oil UP → gold DOWN) requires this: it is not a news event directly causing
gold to move, it is oil's price move that causes money to rotate out of gold. Adding a direct
`CAUSES` edge from `MILITARY_CONFLICT` to `GOLD DOWN` would be wrong: it would predict gold down for
every military conflict regardless of whether oil was involved.

**Decision.** A new `(:Asset)-[:CORRELATES_WITH {condition, direction, weight, confidence, alpha,
beta, last_updated}]->(:Asset)` edge type is added, mirroring the `CAUSES` contract of ADR-006. The
condition is always set (`UPSTREAM_UP` or `UPSTREAM_DOWN`) — there is no unconditional form — and it
gates the edge on the upstream asset's predicted direction in the same pipeline run. The Prediction
Service runs pass 0 for direct `CAUSES` predictions, then a propagation pass: for each asset
predicted directional, it queries `CORRELATES_WITH` edges for the matching condition and runs
`decide()` for the downstream targets. The loop repeats up to `PREDICTION_MAX_PROPAGATION_DEPTH`
(default 3, range 1…10) hops. A `visited: set[AssetId]` per pipeline run prevents cycles.

`decide()` is not changed — it remains single-asset and stateless, and a net ratio below the
deadband produces no prediction. Force summation is preserved on the propagated hop by processing
each depth level in two phases: **collect** every correlation edge reaching each target, then
**decide** each target once with the full edge list. A target joins the visited set only after it is
decided, so two upstream assets converging on it in the same level combine (and can cancel to no
prediction) instead of the first-traversed edge winning. Summation is scoped to one level:
a target already decided at depth *n* is not revised by an edge arriving at depth *n+1*, which is
what the cycle guard requires. Each propagated `PredictionMade` is independently scored by
Verification and independently learned by Credibility: every `CORRELATES_WITH` edge reported in
`contributing_edges` is credited in proportion to its influence, so converging edges share the
evidence rather than each being treated as the sole cause. Direct predictions keep the existing
`CAUSES` credit path unchanged.

The one-edge-per-`(source, target, condition)` triple invariant is enforced by the `MERGE` pattern in
the seed and by application code, **not** by the database. A relationship property existence
constraint (`REQUIRE r.condition IS NOT NULL`) requires Neo4j Enterprise Edition and this stack runs
`neo4j:5.20-community`; an attempt to add one aborted the seed outright. Only a lookup index on
`r.condition` is created, for the propagation query.

Verified live: a `MILITARY_CONFLICT`/`TRANSPORT_AFFECTED` context produced `XOM_NYSE` UP at depth 0,
then `NEM_NYSE` DOWN and `LUG_STO` DOWN at depth 1, with the seeded `NEM_NYSE → XOM_NYSE` back-edge
correctly silenced by the visited set.

**Amended 2026-08-14 (E12 S03): propagation is now gated.** The 2026-08-12 audit found 39 of 113
predictions (35%) were purely propagated and scored 10 correct / 21 wrong, against 27/30 for direct
predictions — materially worse than the mechanism they were derived from. Three guards were added:

1. A confidence floor on the source decision (`PREDICTION_PROPAGATION_MIN_CONFIDENCE`, default 0.30).
2. All direct decisions in a claimed batch are made before any propagation runs, and the visited set is
   seeded with every directly-decided asset. A propagated prediction is also refused when it would
   overturn a standing opposing stance.
3. The depth cap, unchanged.

Guard 2 is the important one, and it closes a hole in the visited-set design recorded above. The guard
is per **context**, and sibling contexts are invisible to each other: one macro event reaches both ends
of an anti-correlated pair, each asset opens its own context in the same batch, and each propagates a
contradiction onto the other. The result was NEM_NYSE holding 16 UP and 13 DOWN predictions, XOM_NYSE 16
DOWN and 13 UP, and LUG_STO 9 and 9 — all from one day's news. No confidence threshold could have caught
this without also switching off propagation for the seeded graph, because the contradicting predictions
were exactly as confident as the ones they contradicted.

**Cost accepted.** Two new graph methods and a propagation loop in the pipeline increase code
surface. The visited-set guard is simple but must be per-run (not global), or it would prevent the
same asset from ever being predicted twice in different runs. Setting
`PREDICTION_MAX_PROPAGATION_DEPTH` too high on a dense graph could increase pipeline latency; the
default of 3 is conservative. Credibility
now has two update paths (`CAUSES` and `CORRELATES_WITH`); they are structurally identical
(Beta-Bernoulli increment) but address different edge types in the graph. And because no DB
constraint backs the condition property, a hand-written Cypher edit could introduce an
unconditioned edge that the propagation query would never match.

## ADR-009: Intraday shadow evidence

Daily close-to-close returns can credit movement that preceded a forecast and miss a valid
post-forecast move that reversed before closing. A target-hit claim and a closing-direction claim
are different experiments, so changing the existing score in place would mix learning evidence.

Keep daily scoring as the live contract while collecting a separate, disabled-by-default intraday
policy. Share one asset/session stream across predictions, persist ordered minute-bar corrections,
and resolve actual exchange sessions. Freeze each prediction's policy and baseline. Unknown or
incomplete evidence is unscorable, not a negative training example. Existing graph learning and
notification consumers cannot receive these shadow results accidentally.

Costs accepted: an exchange-calendar dependency, two additive queues, extra SQL evidence, and
conservative abstention for minute gaps, partial-minute starts and unsupported session breaks.
Yahoo minute coverage and thresholds need empirical validation before any live promotion. Provider
revisions after finalization and exactly-once cross-store learning need a separately reviewed
promotion design; v1 does not claim either is solved by deduplicating messages alone.

## ADR-010: Isolated live point samples with explicit evidence limits

Historical minute coverage was insufficient for the proposed verification experiment. Avanza's
displayed current price can build a prospective dataset, but one observation per 15 minutes cannot
reconstruct OHLC, intraperiod target crossings or an official daily close. Observation time also
cannot prove the age of a delayed quote.

Keep the existing Market Data APIs/providers intact. Add a separate browser worker with a versioned
listing companion, exchange calendars, durable jobs and transactional sample outbox. Discover URLs
once through deterministic identity checks; no LLM or repeated search belongs in the sampling loop.
Verification receives a separate event/queue and computes an OFF/SHADOW sampled policy. Unknown
freshness abstains; results never enter daily learning or notifications.

Costs accepted: browser/runtime maintenance, additional evidence storage and conservative abstention.
Initial anonymous pages report delay without exact quote time, so read success alone does not pass
the verification gate. A multi-session pilot and deployment access validation remain required.
Binding requirements: [MKT-60–MKT-66](SRS-05-market-data.md#avanza-point-observations),
[VER-46–VER-50](SRS-06-verification.md#point-sample-shadow-policy), SHR-94/SHR-95.

## ADR-011: Research evidence before official publication filtering

The official prediction stream omits abstentions and suppressed stances, while context membership
does not retain complete received event versions. Using that stream alone to compare future models
would select the examples using the KG's publication rules and lose input availability evidence.

Optional capture therefore appends event receipt/version evidence and direct context opportunities
inside the Prediction schema before publication filtering. KG research results and official outbox
records are separate; capture has no broker publisher or graph-learning hook. Research failures are
bounded and observable without interrupting official processing. Exports remain explicitly ineligible
for training until point-in-time market features and finalized labels exist.

Costs accepted: extra storage, bounded database latency and incomplete research evidence when capture
fails. Ambiguous event revisions are rejected until context membership can pin their hashes. This
decision establishes evidence capture only; it does not activate a model or change official routing.

## 3. How to update this document

**When to add an ADR** — a decision that changes the system's shape and whose reasoning would not be
obvious from the requirement alone: a new store or engine, a change in who owns a responsibility, a
messaging-topology change, or the reversal of an earlier ADR.

**Steps**

1. Add the record with the next free number (`ADR-012`). Never reuse a number.
2. State the problem that forced the decision, then the decision, then the cost accepted. An ADR with
   no stated cost is usually incomplete.
3. Add a row to the section 2 index, naming the requirements the decision binds to.
4. Add or update those requirements in [SyRS-system.md](SyRS-system.md) or the relevant SRS — the ADR
   explains, it does not bind.
5. To reverse a decision, add a **new** ADR and mark the old one `Superseded by ADR-00N`. Never edit
   superseded reasoning.
6. Add a row to section 4.

## 4. Change history

| Date | Version | Change | Driver |
|---|---|---|---|
| 2026-09-25 | 1.4.0 | ADR-011: separate opt-in research evidence capture from official publication | E15 |
| 2026-09-25 | 1.3.0 | ADR-010: isolated point observations and sampled SHADOW policy | E14 |
| `2026-09-24` | `1.2.0` | ADR-009: independent shadow target-hit evaluation | Intraday verification |
| `2026-08-06` | `1.0.0` | Moved into `requirements/` from `docs/decisions/README.md`. Added ADR-007 to the index (present in the body but missing from the table), requirement-ID cross-references, and update rules | Requirements consolidation |
| `2026-08-12` | `1.1.0` | Added ADR-008: cross-asset `CORRELATES_WITH` propagation | E10 epic |
