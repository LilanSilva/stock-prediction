# S01: Deduplication Pipeline

## Overview

This story builds the first processing stage inside the Cleansing Service: cheap, fast near-duplicate detection that prevents the same wire-syndicated article from being embedded and clustered multiple times. It also extracts (actor, action, object) action signatures from article titles, which are used as Gate 2 of the dual-gate clustering in S02.

Both tasks are intentionally cheap — SimHash runs in microseconds and spaCy NLP is CPU-bound but fast — so they act as a pre-filter before the expensive BGE-m3 embedding step in S02.

---

## Tasks

| ID  | Name                          | Description                                                                                                                         |
|-----|-------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| T01 | SimHash near-duplicate detector | Fingerprint each article with SimHash on title + first 200 chars of body; reject if Hamming distance <= 3 against last 24h window |
| T02 | Action signature extractor    | Use spaCy to extract (actor, action_verb_lemma, object) triple from title; store alongside article for Gate 2 clustering check      |

---

## Dependencies

Before this story can start, the following must exist:

- `src/shared/` package is available with:
  - `ArticleIngested` Pydantic schema
  - RabbitMQ async consumer wrapper
  - Postgres connection helper
- Postgres `events` database is provisioned (see `infra/postgres/`)
- RabbitMQ `raw-news` queue is declared (see `infra/rabbitmq/`)
- E01 (Ingestion Service) is publishing valid `ArticleIngested` messages to `raw-news` — or a test fixture can replay sample messages

---

## How to Test End-to-End

1. Start the full stack: `docker-compose up postgres rabbitmq`
2. Publish two copies of the same article to `raw-news` (identical title, minor body variation simulating wire syndication)
3. Start the Cleansing Service: `docker-compose up cleansing`
4. Verify in Postgres `article_fingerprints` table: only one row exists for that article's fingerprint
5. Verify the second message was acknowledged (not requeued) without being written to `embeddings` or triggering an LLM call
6. Publish an article with a clear subject-verb-object title (e.g., "Iran launches missile strike on Saudi oil facilities")
7. Query Postgres `article_action_signatures` table and confirm `actor=Iran`, `action=launch`, `object=strike` (or similar normalized form)
8. Run unit tests: `pytest services/cleansing/tests/test_dedup.py services/cleansing/tests/test_action_signature.py -v`
