# ADR: Architecture Decision Records

## 1. Document control

| | |
|---|---|
| Document ID | `ADR` |
| Type | Decision record |
| Status | `Implemented` (ADR-001 … ADR-007 all Accepted) |
| Version | `1.0.0` |
| Diagrams | [docs/architectural-documents/](../docs/architectural-documents/) |
| Last verified against code | `2026-08-06` |

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

**Providers are per asset.** No vendor covers every market. biquote serves a curated list of US
mega-caps — probed 2026-08-03, every European listing returns 0 bars, including EU giants on US
exchanges (`ASML`, `SAP`, `NVO`, `SHEL`), plus 8 requested US names. Yahoo covers Stockholm, Euronext
and Xetra, so the registry's `provider` field routes each asset to its adapter. This also corrects the
record: the `HTTP 429`s that retired Yahoo in POC-7 were caused by POC-6's custom User-Agent, not IP
rate limiting (40 concurrent requests with a browser agent all return 200; 8 with curl's default agent
all return 429). Yahoo remains undocumented and UA-sniffing may change, so US assets stay on biquote —
Yahoo flakiness cannot regress existing scoring, and switching to a paid vendor such as Finnhub is one
field in the JSON.

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

## 3. How to update this document

**When to add an ADR** — a decision that changes the system's shape and whose reasoning would not be
obvious from the requirement alone: a new store or engine, a change in who owns a responsibility, a
messaging-topology change, or the reversal of an earlier ADR.

**Steps**

1. Add the record with the next free number (`ADR-008`). Never reuse a number.
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
| `2026-08-06` | `1.0.0` | Moved into `requirements/` from `docs/decisions/README.md`. Added ADR-007 to the index (present in the body but missing from the table), requirement-ID cross-references, and update rules | Requirements consolidation |
