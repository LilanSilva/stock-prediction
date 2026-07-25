# S02: Embedding & Event Clustering

## Overview

This story builds the core intelligence of the Cleansing Service: embedding articles with the BGE-m3 multilingual model, clustering related articles using conservative dual-gate logic, and merging each cluster into a structured event via LLM. The output `EventDetected` messages are what feed all downstream prediction logic.

The dual-gate clustering design is the most critical product requirement in the entire system. Over-merging causally distinct events corrupts the prediction signal. The conservative threshold (cosine >= 0.80 AND matching action verb) is intentionally strict — it is better to create two separate events than to wrongly merge them.

---

## Tasks

| ID  | Name                                   | Description                                                                                                           |
|-----|----------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| T01 | BGE-m3 embedding pipeline              | Load BAAI/bge-m3 at startup; embed title + first 500 chars of body; store 1024-dim vector in Postgres with pgvector  |
| T02 | Conservative dual-gate incremental clustering | pgvector cosine similarity search; merge only if similarity >= 0.80 AND action verb compatible; open new cluster otherwise |
| T03 | LLM event merge & structured extraction | For each ready cluster: call LLM to merge into canonical EventDetected; validate schema; publish to events queue       |

---

## Dependencies

Before this story can start:

- **S01 (Deduplication Pipeline)** must be complete: articles entering S02 have already passed SimHash dedup and have action signatures stored in `article_action_signatures`
- Postgres `events` database has `article_fingerprints` and `article_action_signatures` tables (from S01)
- `pgvector` extension is enabled in Postgres: `CREATE EXTENSION IF NOT EXISTS vector;`
- `src/shared/` package provides:
  - `EventDetected` Pydantic schema
  - Provider-configurable LLM gateway (`shared.llm.LLMGateway`) with `complete_structured(...)`
  - RabbitMQ publisher wrapper
- BAAI/bge-m3 model weights are accessible (either pre-downloaded in Docker image or fetched from HuggingFace Hub on first run)

---

## How to Test End-to-End

1. Start full stack: `docker-compose up postgres rabbitmq cleansing`
2. Publish 3 articles to `raw-news` queue:
   - Article A: "Iran launches missile strike on Saudi Arabia" (English)
   - Article B: "Iran fires missiles at Saudi oil facilities" (English, similar to A — should cluster with A)
   - Article C: "Iran closes Strait of Hormuz" (English, different action — should NOT cluster with A)
3. Wait 35 minutes (or trigger the 30-minute timeout flush manually in tests)
4. Verify `events` queue has 2 messages: one event merging A+B, one event for C alone
5. Validate both messages against `EventDetected` schema using the shared Pydantic model
6. Confirm `source_count == 2` for the A+B merged event and `source_count == 1` for C
7. Run unit tests: `pytest services/cleansing/tests/ -v`
