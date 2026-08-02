"""PostgreSQL access for the Cleansing Service.

Owns the `cleansing` schema application tables. The infra init script only creates the schema and
enables pgvector; each service applies its own table DDL idempotently at startup.

Vectors are passed to/from PostgreSQL using pgvector's text literal form (e.g. "[0.1,0.2]") with an
explicit `::vector` cast, so no extra asyncpg type codec registration is required.
"""

from __future__ import annotations

import asyncpg

EMBEDDING_DIM = 1024

SCHEMA_DDL = f"""
CREATE TABLE IF NOT EXISTS cleansing.article_fingerprints (
    article_id    UUID PRIMARY KEY,
    simhash       TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_fingerprints_published
    ON cleansing.article_fingerprints (published_at);

CREATE TABLE IF NOT EXISTS cleansing.article_embeddings (
    article_id  UUID PRIMARY KEY,
    embedding   vector({EMBEDDING_DIM}) NOT NULL
);

CREATE TABLE IF NOT EXISTS cleansing.article_actions (
    article_id         UUID PRIMARY KEY,
    actor              TEXT,
    action_lemma       TEXT,
    object             TEXT,
    original_lemma     TEXT,
    event_type         TEXT NOT NULL,
    language           TEXT NOT NULL,
    affected_asset_ids TEXT[] NOT NULL DEFAULT '{{}}',
    polarity           TEXT NOT NULL DEFAULT 'OCCURRENCE',
    context_tags       TEXT[] NOT NULL DEFAULT '{{}}'
);

-- Backfill the conditional-causality columns on databases created before they were added; the
-- CREATE above only applies to fresh installs (functional document conditional-causality change).
ALTER TABLE cleansing.article_actions
    ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT 'OCCURRENCE';
ALTER TABLE cleansing.article_actions
    ADD COLUMN IF NOT EXISTS context_tags TEXT[] NOT NULL DEFAULT '{{}}';

CREATE TABLE IF NOT EXISTS cleansing.event_clusters (
    cluster_id        UUID PRIMARY KEY,
    event_type        TEXT NOT NULL,
    state             TEXT NOT NULL,
    article_count     INTEGER NOT NULL DEFAULT 0,
    first_seen_at     TIMESTAMPTZ NOT NULL,
    last_seen_at      TIMESTAMPTZ NOT NULL,
    quiet_deadline    TIMESTAMPTZ NOT NULL,
    lifetime_deadline TIMESTAMPTZ NOT NULL,
    centroid          vector({EMBEDDING_DIM}) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_clusters_open
    ON cleansing.event_clusters (state, event_type)
    WHERE state IN ('OPEN', 'QUIET');

CREATE TABLE IF NOT EXISTS cleansing.cluster_articles (
    cluster_id    UUID NOT NULL REFERENCES cleansing.event_clusters (cluster_id),
    article_id    UUID NOT NULL,
    title         TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL,
    correlation_id UUID NOT NULL,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cluster_id, article_id)
);

CREATE TABLE IF NOT EXISTS cleansing.events (
    event_id          UUID PRIMARY KEY,
    cluster_id        UUID NOT NULL UNIQUE REFERENCES cleansing.event_clusters (cluster_id),
    event_type        TEXT NOT NULL,
    canonical_summary TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    payload           JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cleansing.outbox_events (
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

CREATE INDEX IF NOT EXISTS ix_outbox_events_pending
    ON cleansing.outbox_events (created_at)
    WHERE delivery_status = 'PENDING';
"""


def vector_literal(vector: list[float]) -> str:
    """Render a float vector as a pgvector text literal, e.g. "[0.1,0.2]"."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def parse_vector(text: str) -> list[float]:
    """Parse a pgvector text literal back into a float list."""
    trimmed = text.strip().strip("[]")
    if not trimmed:
        return []
    return [float(part) for part in trimmed.split(",")]


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Create an asyncpg connection pool for the cleansing database."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Create the cleansing tables/indexes idempotently."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
