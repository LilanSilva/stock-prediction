# Documentation Index

All agreed requirements, executable-contract descriptions, architecture decisions, functional documents, diagrams, and current project context for Feed Analyzer.

## Start here

1. [Agreed system requirements](requirements/agreed-system-requirements.md)
2. [Canonical message contracts](contracts/message-contracts.md)
3. [Asset registry](reference/asset-registry.md)
4. [Event taxonomy](reference/event-taxonomy.md)
5. [Architecture decisions](decisions/README.md)
6. [Core acceptance map](requirements/core-acceptance-map.md)
7. [POC-6 result and controlled-rerun plan](../backlog/POC/poc-6-end-to-end-prediction-validation.md)

When older examples conflict, the documents above take precedence. Formal FR/NFR/BR traceability is intentionally not used.

---

## Functional Documents

Detailed specifications for both human developers and coding agents. Each document covers: purpose, functions/methods, data models, message contracts, configuration, error handling, and acceptance criteria.

| File | What it covers |
|---|---|
| [functional-documents/system-overview-functional-document.md](functional-documents/system-overview-functional-document.md) | Full system end-to-end: features, lifecycle, configuration, and failure modes |
| [functional-documents/ingestion-service-functional-document.md](functional-documents/ingestion-service-functional-document.md) | Small-source-set polling, safe body fetching, normalization, idempotency, and outbox publishing |
| [functional-documents/cleansing-service-functional-document.md](functional-documents/cleansing-service-functional-document.md) | SimHash dedup, BGE-m3 embeddings, lifecycle-aware clustering, and conditional LLM assistance |
| [functional-documents/prediction-service-functional-document.md](functional-documents/prediction-service-functional-document.md) | Multi-event contexts, graph-only M1 decisions, deferred LLM conflict arbitration, and prediction storage |
| [functional-documents/market-data-service-functional-document.md](functional-documents/market-data-service-functional-document.md) | Durable dual-session requests, canonical/provider mapping, and immutable close observations |
| [functional-documents/verification-service-functional-document.md](functional-documents/verification-service-functional-document.md) | Evaluation scheduling, close-to-close scoring, 0.3% deadband, and idempotent publication |
| [functional-documents/credibility-service-functional-document.md](functional-documents/credibility-service-functional-document.md) | Duplicate-safe, separated edge/arbiter/source learning with a common 1/1 prior |
| [functional-documents/api-gateway-functional-document.md](functional-documents/api-gateway-functional-document.md) | Read-only REST BFF, dedicated live queues, health/readiness, local-only security |
| [functional-documents/dashboard-functional-document.md](functional-documents/dashboard-functional-document.md) | Prediction audit views, score/credibility visualization, and live-update behavior |

---

## Architecture Diagrams

Open `.mmd` files with the **Mermaid Preview** VS Code plugin (`Ctrl+Shift+P` → "Mermaid: Open Preview").

| File | What it shows |
|---|---|
| [01-architecture-components.mmd](architectural-documents/01-architecture-components.mmd) | Components, schemas, graph store, and connections |
| [02-architecture-queues.mmd](architectural-documents/02-architecture-queues.mmd) | Topic exchange and independent consumer queues |
| [03-sequence-prediction-pipeline.mmd](architectural-documents/03-sequence-prediction-pipeline.mmd) | Ingestion, cleansing, multi-event context, and token-efficient prediction |
| [04-sequence-verification-credibility.mmd](architectural-documents/04-sequence-verification-credibility.mmd) | Dual-session price request, scoring, and idempotent learning |
| [05-sequence-dashboard.mmd](architectural-documents/05-sequence-dashboard.mmd) | Read-only API access and live fan-out |

---

## Quick Reference

### Database stack (2 engines only)

| Engine | Used by | Why |
|---|---|---|
| **Postgres** + `pgvector` | All services and read-only BFF | One database with service-owned schemas |
| **Neo4j** | Prediction + Credibility | Knowledge graph — causal edges, signed weights, multi-hop traversal |

### Message contracts

| Message | Published by | Routing key |
|---|---|---|
| `ArticleIngested` | Ingestion | `article.ingested` |
| `EventDetected` | Cleansing | `event.detected` |
| `PredictionMade` | Prediction | `prediction.made` |
| `PriceRequested` | Verification | `price.requested` |
| `PriceObserved` | Market Data | `price.observed` |
| `PredictionScored` | Verification | `prediction.scored` |

### Two rules that explain the whole system

1. Backend services communicate through the `feed.events` exchange and independent consumer queues, never by consuming another service's work queue.
2. Local deterministic processing comes first; in M1 the LLM is used only for ambiguous cleansing or factual-conflict resolution through the provider-configurable shared LLM gateway.
