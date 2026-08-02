# Canonical Message Contracts

## Authority and conventions

Future Pydantic models in `shared.schemas` are the executable source of truth. This document defines the agreed initial contract that those models must implement.

All messages include:

| Field | Type | Purpose |
|---|---|---|
| `message_id` | UUID | Unique delivery/idempotency identifier |
| `correlation_id` | UUID | End-to-end business-flow correlation |
| `causation_id` | UUID or null | Message that caused this message |
| `occurred_at` | UTC datetime | Time the domain event occurred |
| `schema_version` | string | Initial value `1.0` |

Business timestamps are timezone-aware UTC. Enum values use uppercase snake case. Asset values are canonical asset IDs.

## Exchange and bindings

Durable topic exchange: `feed.events`.

| Routing key | Producer | Consumer queue |
|---|---|---|
| `article.ingested` | Ingestion | `cleansing.articles` |
| `event.detected` | Cleansing | `prediction.events` |
| `prediction.made` | Prediction | `verification.predictions` |
| `prediction.made` | Prediction | `gateway.predictions.live` |
| `price.requested` | Verification | `market-data.price-requests` |
| `price.observed` | Market Data | `verification.prices` |
| `prediction.scored` | Verification | `credibility.scored` |
| `prediction.scored` | Verification | `gateway.scored.live` |

Service work queues are durable. Gateway live queues may be non-durable and auto-delete because REST supplies catch-up state. Every durable work queue has a dedicated DLQ named `<queue>.dlq`.

## ArticleIngested

Routing key: `article.ingested`.

| Field | Type | Required |
|---|---|---|
| `article_id` | UUID | Yes |
| `source_id` | string | Yes |
| `canonical_url` | URL | Yes |
| `title` | string | Yes |
| `body` | string | Yes |
| `published_at` | UTC datetime | Yes |
| `language` | ISO 639-1 string | Yes |
| `country` | ISO 3166-1 alpha-2 string | Yes |
| `content_hash` | string | Yes |

Raw HTML is not published. If legally retained, it stays in the Ingestion schema with a bounded size and retention policy.

## EventDetected

Routing key: `event.detected`.

```text
SourceRef:
  article_id: UUID
  source_id: string
  canonical_url: URL
  title: string
  published_at: datetime

FactConflict:
  field: string
  values: list[{source_id: string, value: string}]
  resolution: string | null
```

| Field | Type | Required |
|---|---|---|
| `event_id` | UUID | Yes |
| `cluster_id` | UUID | Yes |
| `canonical_summary` | string | Yes |
| `event_type` | EventType | Yes |
| `actor` | string or null | Yes |
| `action` | string or null | Yes |
| `object` | string or null | Yes |
| `entities` | list[string] | Yes |
| `affected_asset_ids` | list[AssetId] | Yes |
| `polarity` | `OCCURRENCE` or `RESOLUTION` | No (default `OCCURRENCE`) |
| `context_tags` | list[ConditionCode] | No (default `[]`) |
| `first_seen_at` | UTC datetime | Yes |
| `last_seen_at` | UTC datetime | Yes |
| `sources` | list[SourceRef] | Yes |
| `fact_conflicts` | list[FactConflict] | Yes |
| `extraction_method` | `LOCAL` or `LLM_ASSISTED` | Yes |
| `llm_metadata` | LlmMetadata or null | Yes |

## PredictionMade

Routing key: `prediction.made`.

```text
ContributingEdge:
  edge_id: string  # "FACTOR->ASSET" (unconditional) or "FACTOR|CONDITION->ASSET" (conditioned)
  direction: UP | DOWN | NEUTRAL
  current_weight: float [0,1]
  influence_weight: float [0,1]
  path: string

LlmMetadata:
  prompt_version: string
  model: string
  context_hash: string
  input_tokens: integer
  output_tokens: integer
  latency_ms: integer
  attempt_count: integer
  status: SUCCESS | FAILED
```

| Field | Type | Required |
|---|---|---|
| `prediction_id` | UUID | Yes |
| `context_id` | UUID | Yes |
| `context_version` | integer | Yes |
| `event_ids` | list[UUID] | Yes |
| `asset_id` | AssetId | Yes |
| `direction` | `UP`, `DOWN`, or `NEUTRAL` | Yes |
| `magnitude` | `SMALL`, `MEDIUM`, or `LARGE` | Yes |
| `confidence` | float [0,1] | Yes |
| `horizon` | `ONE_TRADING_DAY` | Yes |
| `rationale` | bounded string | Yes |
| `contributing_edges` | list[ContributingEdge] | Yes |
| `decision_at` | UTC datetime | Yes |
| `supersedes_prediction_id` | UUID or null | Yes |
| `decision_method` | `GRAPH_ONLY` or `LLM_ARBITRATED` | Yes |
| `llm_metadata` | LlmMetadata or null | Yes |

## PriceRequested

Routing key: `price.requested`. Producer: Verification.

| Field | Type | Required |
|---|---|---|
| `request_id` | UUID | Yes |
| `prediction_id` | UUID | Yes |
| `asset_id` | AssetId | Yes |
| `baseline_session` | local market date | Yes |
| `settlement_session` | local market date | Yes |
| `market_calendar` | string | Yes |

The message is published once per prediction evaluation. Duplicate requests reuse the same deterministic request ID.

## PriceObserved

Routing key: `price.observed`.

```text
CloseObservation:
  session: local market date
  close: positive decimal
  provider_bar_time: UTC datetime or null
  fetched_at: UTC datetime
  source: string
  provider_symbol: string
  price_kind: PROVIDER_DAILY_CLOSE | OFFICIAL_SETTLEMENT
  is_adjusted: boolean
  registry_version: string
```

| Field | Type | Required |
|---|---|---|
| `request_id` | UUID | Yes |
| `prediction_id` | UUID | Yes |
| `asset_id` | AssetId | Yes |
| `baseline` | CloseObservation | Yes |
| `settlement` | CloseObservation | Yes |

`session` is the approved local-market session key. `provider_bar_time` preserves the timestamp supplied by the provider and must not be presented as a settlement observation time unless `price_kind=OFFICIAL_SETTLEMENT`. `fetched_at` records when the adapter acquired the data. For the local POC, Yahoo daily bars are `PROVIDER_DAILY_CLOSE`.

## PredictionScored

Routing key: `prediction.scored`.

| Field | Type | Required |
|---|---|---|
| `prediction_id` | UUID | Yes |
| `context_id` | UUID | Yes |
| `asset_id` | AssetId | Yes |
| `predicted_direction` | Direction | Yes |
| `actual_direction` | Direction | Yes |
| `predicted_magnitude` | Magnitude | Yes |
| `actual_magnitude` | Magnitude | Yes |
| `confidence` | float [0,1] | Yes |
| `actual_return` | float | Yes |
| `is_correct` | boolean | Yes |
| `score` | float [0,1] | Yes |
| `contributing_edges` | list[ContributingEdge] | Yes |
| `source_ids` | list[string] | Yes |
| `baseline` | CloseObservation | Yes |
| `settlement` | CloseObservation | Yes |
| `scored_at` | UTC datetime | Yes |

## Conditional causality

`EventDetected.polarity` and `EventDetected.context_tags` qualify how an event maps to the causal
graph. Both are optional with backward-compatible defaults (`OCCURRENCE`, `[]`), so pre-existing
producers/consumers stay valid within major version 1.

- `EventPolarity`: `OCCURRENCE` (factor onset — default) or `RESOLUTION` (de-escalation/negation).
  A `RESOLUTION` event inverts the sign of the factor's causal edge at decision time (e.g. a
  called-off conflict turns an oil-up edge into an oil-down force).
- `ConditionCode`: `TRANSPORT_AFFECTED`, `SAFE_HAVEN_ONLY`, `RISK_PREMIUM_ELEVATED`. Conditions gate
  which causal edge fires. `RISK_PREMIUM_ELEVATED` is derived at decision time by Prediction from
  recent price history (Market Data `GET /prices/recent`), not by Cleansing.

### Causal graph schema

The condition is a property on the `CAUSES` edge, so each `(factor, asset, condition)` is a distinct
edge with its own weight and Beta-Bernoulli reliability:

```text
(:CausalFactor {id})-[:CAUSES {condition, direction, weight, confidence, alpha, beta,
                               last_updated}]->(:Asset {id})
```

An edge with no `condition` property is unconditional and always fires. The condition is a plain
edge property whose values are the canonical `ConditionCode` enum, so no separate node type is
needed. The `ContributingEdge.edge_id` business key is `FACTOR->ASSET` for
unconditional edges and `FACTOR|CONDITION->ASSET` for conditioned edges. Edge weights are refined
online by Credibility (per scored prediction) and offline by the structure learner
(`python -m credibility.learning.run`) which mines historical events against realized price moves.

## Compatibility

- Adding an optional field with a default is backward-compatible within major version 1.
- Removing, renaming, changing a type, or changing enum meaning requires a major version.
- Producers and consumers must have contract tests for all supported versions.
- Unknown major versions are dead-lettered with `unsupported_schema_version` metadata.
