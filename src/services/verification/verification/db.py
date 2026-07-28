"""PostgreSQL access for the ``verification`` schema: pool creation and idempotent DDL."""

from __future__ import annotations

import asyncpg

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS verification;

-- One immutable evaluation per prediction. request_id is the deterministic dual-session price
-- request identity (unique per evaluation), so duplicate PredictionMade creates no second request.
CREATE TABLE IF NOT EXISTS verification.evaluations (
    prediction_id        UUID PRIMARY KEY,
    context_id           UUID NOT NULL,
    asset_id             TEXT NOT NULL,
    predicted_direction  TEXT NOT NULL,
    predicted_magnitude  TEXT NOT NULL,
    confidence           DOUBLE PRECISION NOT NULL,
    decision_at          TIMESTAMPTZ NOT NULL,
    baseline_session     DATE NOT NULL,
    settlement_session   DATE NOT NULL,
    market_calendar      TEXT NOT NULL,
    registry_version     TEXT NOT NULL,
    request_id           UUID NOT NULL UNIQUE,
    correlation_id       UUID NOT NULL,
    contributing_edges   JSONB NOT NULL DEFAULT '[]',
    source_ids           JSONB NOT NULL DEFAULT '[]',
    status               TEXT NOT NULL DEFAULT 'PENDING',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS evaluations_status_idx ON verification.evaluations (status);

-- The two immutable closes returned in one PriceObserved (kept for the audit record).
CREATE TABLE IF NOT EXISTS verification.price_observations (
    request_id     UUID PRIMARY KEY,
    prediction_id  UUID NOT NULL,
    asset_id       TEXT NOT NULL,
    baseline       JSONB NOT NULL,
    settlement     JSONB NOT NULL,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One immutable score per prediction (unique score identity).
CREATE TABLE IF NOT EXISTS verification.scores (
    prediction_id       UUID PRIMARY KEY,
    asset_id            TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,
    actual_direction    TEXT NOT NULL,
    predicted_magnitude TEXT NOT NULL,
    actual_magnitude    TEXT NOT NULL,
    confidence          DOUBLE PRECISION NOT NULL,
    actual_return       DOUBLE PRECISION NOT NULL,
    is_correct          BOOLEAN NOT NULL,
    score               DOUBLE PRECISION NOT NULL,
    scored_at           TIMESTAMPTZ NOT NULL
);

-- Transactional outbox carrying both PriceRequested and PredictionScored (dispatched by type).
CREATE TABLE IF NOT EXISTS verification.outbox_events (
    message_id       UUID PRIMARY KEY,
    aggregate_id     UUID NOT NULL,
    message_type     TEXT NOT NULL,
    payload          TEXT NOT NULL,
    delivery_status  TEXT NOT NULL DEFAULT 'PENDING',
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at     TIMESTAMPTZ
);
"""


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Create an asyncpg connection pool for the verification schema."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Apply the idempotent DDL that owns the ``verification`` schema."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
