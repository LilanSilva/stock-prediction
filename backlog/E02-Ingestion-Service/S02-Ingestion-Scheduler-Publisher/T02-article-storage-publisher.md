# T02: Raw Article Storage & Deduplication

## Context

After the hourly job fetches and body-enriches articles (S02-T01), this component persists each new article to Postgres and publishes an `ArticleIngested` message to the `raw-news` RabbitMQ queue. URL-based deduplication ensures the same article is never stored or published twice — even across service restarts or repeated hourly polls. This task is the final stage of the Ingestion pipeline and the hand-off point to the Cleansing Service.

## Background

**Deduplication strategy**: Use a unique index on `raw_news.url` and `INSERT ... ON CONFLICT (url) DO NOTHING`. This is the simplest reliable dedup mechanism — no in-memory state is needed across restarts.

**Publish-after-insert**: Only publish `ArticleIngested` to RabbitMQ for rows that were actually inserted (not skipped by the conflict). Use the `RETURNING article_id` clause (or check `rowcount`) to determine which articles are genuinely new.

**Message schema**: `ArticleIngested` is defined in `src/shared/schemas.py` as a Pydantic `BaseModel`. All field names must match exactly.

**Queue name**: `raw-news` (hyphenated, not underscored — RabbitMQ queue names are case-sensitive).

File locations:
- `src/services/ingestion/db/raw_news.py` — `RawNewsRepository` class
- `src/services/ingestion/publisher.py` — `ArticleIngestedPublisher` class
- `src/services/ingestion/store_publisher.py` — `ArticleStorePublisher` orchestration class (called by S02-T01)

## Inputs

- `articles: list[RawArticle]` — enriched articles from `ArticleBodyFetcher.enrich()` (S01-T03)
- Postgres `raw_news` table (must exist via migration)
- RabbitMQ `raw-news` queue (auto-declared if not present)
- `shared.schemas.ArticleIngested` Pydantic model
- `shared.rabbitmq.RabbitMQPublisher` aio-pika wrapper

## Outputs

### Postgres: `raw_news` Table
```sql
CREATE TABLE raw_news (
    article_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source        VARCHAR(50) NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    published_at  TIMESTAMPTZ NOT NULL,
    language      CHAR(2) NOT NULL,
    country       CHAR(2) NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    correlation_id UUID NOT NULL,
    raw_html      TEXT
);
CREATE UNIQUE INDEX raw_news_url_idx ON raw_news (url);
```

### RabbitMQ: `raw-news` queue
One `ArticleIngested` message per **newly inserted** article (skip for conflict-skipped rows).

`ArticleIngested` schema (from `src/shared/schemas.py`):
```python
class ArticleIngested(BaseModel):
    article_id:    UUID
    source:        str
    url:           str
    title:         str
    body:          str
    published_at:  datetime
    language:      str
    country:       str
    raw_html:      str | None
    correlation_id: UUID
```

## Technical Requirements

### Libraries
- `sqlalchemy[asyncio]` >= 2.0 with `asyncpg` dialect
- `aio-pika` >= 9.x — async RabbitMQ client
- `pydantic` >= 2.x — message schema validation
- `uuid` standard library — `uuid.uuid4()` for `article_id` and `correlation_id`

### `RawNewsRepository`
```python
# src/services/ingestion/db/raw_news.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

class RawNewsRepository:
    def __init__(self, session: AsyncSession) -> None: ...

    async def insert_new_articles(
        self, articles: list[RawArticle]
    ) -> list[RawArticle]:
        """
        Bulk-insert articles using ON CONFLICT (url) DO NOTHING.
        Returns only the articles that were actually inserted (not skipped).
        """
        ...
```

Implementation pattern:
```python
stmt = pg_insert(RawNewsORM).values(
    [{"article_id": uuid4(), "url": a.url, "title": a.title, ...} for a in articles]
).on_conflict_do_nothing(index_elements=["url"]).returning(RawNewsORM.url)

result = await session.execute(stmt)
await session.commit()
inserted_urls = {row.url for row in result.fetchall()}
return [a for a in articles if a.url in inserted_urls]
```

### `ArticleIngestedPublisher`
```python
# src/services/ingestion/publisher.py
import aio_pika
from shared.schemas import ArticleIngested

class ArticleIngestedPublisher:
    QUEUE_NAME = "raw-news"

    def __init__(self, connection: aio_pika.abc.AbstractRobustConnection) -> None: ...

    async def publish(self, articles: list[RawArticle]) -> None:
        """Publish one ArticleIngested message per article."""
        ...
```

Publish pattern:
```python
channel = await self.connection.channel()
await channel.declare_queue(self.QUEUE_NAME, durable=True)
exchange = channel.default_exchange

for article in articles:
    msg = ArticleIngested(
        article_id=article.article_id,  # set during DB insert
        source=article.source_name,
        url=article.url,
        title=article.title,
        body=article.body or "",
        published_at=article.published_at,
        language=article.language,
        country=article.country,
        raw_html=article.raw_html,
        correlation_id=uuid4(),
    )
    await exchange.publish(
        aio_pika.Message(
            body=msg.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=self.QUEUE_NAME,
    )
```

### `ArticleStorePublisher` (orchestration)
```python
# src/services/ingestion/store_publisher.py
class ArticleStorePublisher:
    def __init__(
        self,
        repo: RawNewsRepository,
        publisher: ArticleIngestedPublisher,
    ) -> None: ...

    async def store_and_publish(self, articles: list[RawArticle]) -> int:
        """Insert new articles and publish messages. Returns count of new articles."""
        new_articles = await self.repo.insert_new_articles(articles)
        await self.publisher.publish(new_articles)
        return len(new_articles)
```

### RabbitMQ Connection
- Use `aio_pika.connect_robust(RABBITMQ_URL)` — robust connections auto-reconnect on broker restart.
- Create the connection in the FastAPI lifespan (S02-T01) and inject into `ArticleIngestedPublisher`.
- Declare the queue as `durable=True` so messages survive RabbitMQ restarts.

### `correlation_id`
Generate a fresh `uuid4()` per `ArticleIngested` message. This is used for tracing the article through downstream services (Cleansing, Prediction). Log the `correlation_id` alongside the `article_id` in the publish log line.

## Acceptance Criteria

1. `store_and_publish(articles)` inserts all articles into `raw_news` and returns the count of newly inserted rows.
2. Re-running `store_and_publish` with the same articles returns `0` (no duplicates inserted, no duplicate messages published).
3. Only articles that were actually inserted (not conflict-skipped) produce `ArticleIngested` messages on `raw-news`.
4. Each `ArticleIngested` message is valid JSON parseable by `ArticleIngested.model_validate_json()`.
5. All fields in `ArticleIngested` are populated; `body` defaults to `""` (empty string, not `null`) when the article has no body.
6. The `raw-news` queue is declared as `durable=True`.
7. Messages are published with `delivery_mode=PERSISTENT`.
8. The Postgres `raw_news.url` unique index prevents duplicate rows even under concurrent inserts.
9. Unit test: given 5 articles where 2 URLs already exist in the DB, `store_and_publish` inserts exactly 3 rows and publishes exactly 3 messages.
10. `mypy --strict` and `ruff` report zero errors on all files in this task.

## Implementation Notes

- **Bulk insert vs. row-by-row**: Use a single `INSERT ... VALUES (...), (...) ON CONFLICT DO NOTHING` statement for the entire batch. Do NOT loop and insert one at a time — this is 50-250x slower for a typical hourly batch of 50-250 articles.
- **`RETURNING url`**: PostgreSQL's `ON CONFLICT DO NOTHING` skips conflicting rows and does NOT include them in `RETURNING`. This means `RETURNING article_id` gives you only the newly inserted IDs — use this to filter which articles to publish.
- **SQLAlchemy ORM vs Core**: Use SQLAlchemy Core (`pg_insert()`) for the bulk upsert, not the ORM `session.add()` pattern. The Core API has first-class support for `ON CONFLICT` clauses.
- **`article_id` assignment**: Generate `uuid4()` for each article in the `values` dict during the insert. After the `RETURNING` query, enrich the `RawArticle` objects with their assigned `article_id` before passing to the publisher.
- **Message ordering**: aio-pika publishes to the default exchange (direct queue routing). Messages arrive in the order published. No special ordering config needed.
- **Error on publish failure**: If RabbitMQ publish fails after DB insert, log ERROR with the `article_id` list. Do NOT roll back the DB insert — it is safer to have DB rows without queue messages (the health endpoint can detect this) than to lose data. A future retry mechanism can re-publish from DB.
- **`body` never null in message**: The `ArticleIngested` schema has `body: str`, not `str | None`. Coerce `None` to `""` before constructing the Pydantic model.

## Definition of Done

- [x] Storage/outbox covered by tests (`tests/test_integration.py`, `tests/test_pipeline*.py`)
- [x] Integration test: storing the same canonical URL twice is idempotent, and the outbox delivers to RabbitMQ (verified live)
- [x] `ruff check` reports zero issues on `ingestion/db.py` and `ingestion/storage.py`
- [x] `mypy --strict` reports zero errors
- [ ] Alembic migration — superseded: idempotent `CREATE TABLE IF NOT EXISTS` applied at startup
- [x] Unique index present — `ingestion.articles.canonical_url` is UNIQUE
- [x] All `ArticleIngested` messages are valid against the shared schema (outbox uses `model_validate_json`)
- [x] `PERSISTENT` delivery verified (shared `RabbitMQClient` test); work queues are durable via `definitions.json`
- [ ] Bulk `on_conflict_do_nothing` insert — superseded: per-article `INSERT ... ON CONFLICT (canonical_url) DO NOTHING`
- [x] `correlation_id` is a fresh UUID per message (each article is the root of its own causal chain)
