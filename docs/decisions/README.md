# Architecture Decision Records

Only lightweight ADRs are maintained for decisions that materially shape the POC. Formal FR/NFR/BR identifier traceability is intentionally not used.

| ADR | Decision | Status |
|---|---|---|
| ADR-001 | Multi-event context before prediction | Accepted |
| ADR-002 | One PostgreSQL database with service-owned schemas | Accepted |
| ADR-003 | Topic exchange with one queue per consumer | Accepted |
| ADR-004 | Verification owns evaluation and one dual-session price request | Accepted |
| ADR-005 | Token-efficient conditional LLM use | Accepted |

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
