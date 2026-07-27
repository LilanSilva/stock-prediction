"""PostgreSQL access for the Ingestion Service.

Owns the `ingestion` schema application tables. Per the topology, each service applies its own table
DDL; this module creates them idempotently at startup (the infra init script only creates schemas).
"""

from __future__ import annotations

import asyncpg

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS ingestion.articles (
    article_id     UUID PRIMARY KEY,
    source_id      TEXT NOT NULL,
    canonical_url  TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    published_at   TIMESTAMPTZ NOT NULL,
    language       TEXT NOT NULL,
    country        TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_articles_content_hash
    ON ingestion.articles (content_hash);

CREATE TABLE IF NOT EXISTS ingestion.outbox (
    id              BIGSERIAL PRIMARY KEY,
    message_id      UUID NOT NULL UNIQUE,
    aggregate_id    UUID NOT NULL,
    routing_key     TEXT NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivery_status TEXT NOT NULL DEFAULT 'PENDING',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    delivered_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_outbox_pending
    ON ingestion.outbox (created_at)
    WHERE delivery_status = 'PENDING';
"""


async def create_pool(
    dsn: str, *, min_size: int = 1, max_size: int = 5
) -> asyncpg.Pool:
    """Create an asyncpg connection pool for the ingestion database."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Create the ingestion tables/indexes idempotently."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
