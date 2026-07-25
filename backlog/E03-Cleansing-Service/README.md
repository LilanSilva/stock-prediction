# E03: Cleansing Service

> Contract-freeze status: this epic and its child tasks are governed by the [backlog override matrix](../contract-freeze-overrides.md). Conflicting legacy details are non-authoritative until re-slicing.

## Overview

The Cleansing Service is the second stage in the news-driven stock market prediction pipeline. It consumes raw articles from the `raw-news` queue, eliminates near-duplicate wire syndication copies, clusters semantically related articles into unified events using BGE-m3 multilingual embeddings, and merges each cluster into a structured `EventDetected` message via LLM. The output is published to the `events` queue for downstream consumption by the Prediction Service.

The service addresses a fundamental problem in news aggregation: the same event is often covered by dozens of outlets with near-identical wording (wire syndication), and naive deduplication would miss semantically equivalent but differently-worded articles. Conversely, over-aggressive merging would collapse causally distinct events into one, corrupting the prediction signal. The dual-gate conservative clustering design is the critical product requirement that prevents this.

---

## Stories

| ID  | Name                          | Description                                                                                  |
|-----|-------------------------------|----------------------------------------------------------------------------------------------|
| S01 | Deduplication Pipeline        | SimHash near-duplicate detection and action-signature extraction for Gate 2 of clustering    |
| S02 | Embedding & Event Clustering  | BGE-m3 multilingual embeddings, conservative dual-gate incremental clustering, LLM event merge |

---

## Architecture Context

**Service:** `services/cleansing/`

**Consumes queue:** `raw-news`
- Message: `ArticleIngested` `{article_id, source, url, title, body, published_at, language, country, raw_html, correlation_id}`

**Publishes queue:** `events`
- Message: `EventDetected` `{event_id, canonical_summary, event_type, actor, action, object, entities[], affected_assets[], first_seen, source_count, sources[], fact_conflicts[], correlation_id}`

**Databases:**
- **Postgres** (`events` DB, pgvector extension): stores article fingerprints, embeddings (`vector(1024)`), cluster assignments, and serialized events
- **No Neo4j usage** in this service (Neo4j is used by Prediction and Credibility services only)

**Message bus client:** `aio-pika` (async RabbitMQ)

**Key external dependencies:**
- `datasketch` - MinHash/SimHash fingerprinting for deduplication
- `spaCy` with `en_core_web_sm` and `sv_core_news_sm` - action-signature extraction
- `sentence-transformers` with `BAAI/bge-m3` - 1024-dim multilingual embeddings
- `pgvector` - cosine similarity nearest-neighbour search in Postgres

**Shared library:** `src/shared/` package provides Pydantic schemas, RabbitMQ wrapper, LLM gateway, and logging utilities.

---

## Overall Acceptance Criteria

1. Articles published to `raw-news` queue are consumed within 5 seconds of arrival under normal load.
2. Duplicate wire copies of the same article (Hamming distance <= 3 on SimHash fingerprint) are silently dropped; only the first copy is processed further.
3. Articles with cosine similarity >= 0.80 AND matching action verb lemma are merged into the same cluster; articles failing either gate open a new cluster.
4. Each cluster of >= 2 articles (or any single article after 30-minute timeout) triggers an LLM merge call and produces exactly one `EventDetected` message.
5. Published `EventDetected` messages validate against the `EventDetected` Pydantic schema with no missing required fields.
6. The BGE-m3 model is loaded once at startup and reused for all embedding requests; no cold-start penalty per article.
7. Swedish-language articles are handled correctly by both spaCy (`sv_core_news_sm`) and BGE-m3 (multilingual model).
8. All Postgres writes are idempotent — reprocessing the same `article_id` does not produce duplicate rows.
9. Service recovers gracefully from RabbitMQ disconnects and Postgres transient errors without losing messages (aio-pika `ack` only after successful processing).
10. Unit test coverage >= 80% for deduplication, action extraction, clustering logic, and LLM prompt construction.
