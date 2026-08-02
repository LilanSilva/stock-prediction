# Architecture Decision Records

Only lightweight ADRs are maintained for decisions that materially shape the POC. Formal FR/NFR/BR identifier traceability is intentionally not used.

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Multi-event context before prediction | Accepted |
| ADR-002 | One PostgreSQL database with service-owned schemas | Accepted |
| ADR-003 | Topic exchange with one queue per consumer | Accepted |
| ADR-004 | Verification owns evaluation and one dual-session price request | Accepted |
| ADR-005 | Token-efficient conditional LLM use | Accepted |
| ADR-006 | Conditional causal graph with event polarity and offline structure learning | Accepted |

## ADR-001: Multi-event context before prediction

Prediction groups distinct events by canonical asset and event-time window. One immutable prediction is produced per asset/context version. Late events may create a superseding version.

## ADR-002: One database, schema ownership

The POC uses one PostgreSQL database to permit read-only BFF joins while preserving ownership through schemas and roles. Database-per-service may be reconsidered only when independent deployment needs justify it.

## ADR-003: Event exchange and consumer queues

Producers publish to `feed.events`. Each business consumer and live-update observer uses its own bound queue. No observer consumes another service's work queue.

## ADR-004: Verification owns evaluation

Prediction publishes the forecast. Verification resolves baseline and settlement sessions and publishes one request containing both. Market Data returns both approved immutable reference closes with explicit price kind and provider metadata.

## ADR-005: Conditional LLM use

Local deterministic processing is the default. For M1, LLM use is restricted to ambiguous cleansing/factual-conflict resolution. The shared LLM gateway is provider-configurable through environment settings and API keys, so the project can switch models/providers without changing service business logic. Prediction-time LLM arbitration remains deferred after the POC-6 `STOP` result unless a new controlled hypothesis is approved.

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
