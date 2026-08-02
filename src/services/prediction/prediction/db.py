"""PostgreSQL access for the ``prediction`` schema: pool creation and idempotent DDL."""

from __future__ import annotations

import asyncpg

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS prediction;

-- One versioned, per-asset event-time context window. A new version is created for eligible late
-- events; previously published predictions stay immutable (functional document sec 3).
CREATE TABLE IF NOT EXISTS prediction.contexts (
    context_id       UUID PRIMARY KEY,
    asset_id         TEXT NOT NULL,
    window_start     TIMESTAMPTZ NOT NULL,
    window_end       TIMESTAMPTZ NOT NULL,
    context_version  INTEGER NOT NULL,
    state            TEXT NOT NULL DEFAULT 'OPEN',
    watermark        TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, window_start, context_version)
);

CREATE INDEX IF NOT EXISTS contexts_ready_idx
    ON prediction.contexts (state, window_end);

-- Distinct event membership. The composite PK makes duplicate delivery idempotent.
CREATE TABLE IF NOT EXISTS prediction.context_events (
    context_id     UUID NOT NULL REFERENCES prediction.contexts(context_id) ON DELETE CASCADE,
    event_id       UUID NOT NULL,
    event_type     TEXT NOT NULL,
    first_seen_at  TIMESTAMPTZ NOT NULL,
    polarity       TEXT NOT NULL DEFAULT 'OCCURRENCE',
    context_tags   TEXT[] NOT NULL DEFAULT '{}',
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (context_id, event_id)
);

-- Conditional-causality columns for already-provisioned databases (idempotent).
ALTER TABLE prediction.context_events
    ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT 'OCCURRENCE';
ALTER TABLE prediction.context_events
    ADD COLUMN IF NOT EXISTS context_tags TEXT[] NOT NULL DEFAULT '{}';

-- One immutable prediction per ready asset/context version. The idempotency key
-- (asset_id, window_start, horizon, context_version) prevents duplicate identities.
CREATE TABLE IF NOT EXISTS prediction.predictions (
    prediction_id             UUID PRIMARY KEY,
    context_id                UUID NOT NULL REFERENCES prediction.contexts(context_id),
    context_version           INTEGER NOT NULL,
    asset_id                  TEXT NOT NULL,
    direction                 TEXT NOT NULL,
    magnitude                 TEXT NOT NULL,
    confidence                DOUBLE PRECISION NOT NULL,
    horizon                   TEXT NOT NULL,
    rationale                 TEXT NOT NULL,
    decision_method           TEXT NOT NULL,
    decision_at               TIMESTAMPTZ NOT NULL,
    supersedes_prediction_id  UUID,
    idempotency_key           TEXT NOT NULL UNIQUE,
    status                    TEXT NOT NULL DEFAULT 'PENDING',
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS prediction.contributing_edges (
    prediction_id     UUID NOT NULL
                          REFERENCES prediction.predictions(prediction_id) ON DELETE CASCADE,
    edge_id           TEXT NOT NULL,
    direction         TEXT NOT NULL,
    current_weight    DOUBLE PRECISION NOT NULL,
    influence_weight  DOUBLE PRECISION NOT NULL,
    path              TEXT NOT NULL
);

-- Transactional outbox for prediction.made (persist-before-publish; reconciled after restart).
CREATE TABLE IF NOT EXISTS prediction.outbox_events (
    message_id       UUID PRIMARY KEY,
    aggregate_id     UUID NOT NULL,
    payload          TEXT NOT NULL,
    delivery_status  TEXT NOT NULL DEFAULT 'PENDING',
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at     TIMESTAMPTZ
);
"""


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Create an asyncpg connection pool for the prediction schema."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Apply the idempotent DDL that owns the ``prediction`` schema."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
