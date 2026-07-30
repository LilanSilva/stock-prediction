"""PostgreSQL access for the ``credibility`` schema: pool creation and idempotent DDL.

The schema itself is created by infra/postgres (``CREATE SCHEMA credibility AUTHORIZATION
feed_user``); this module owns only the application tables inside it, applied idempotently at
startup exactly like the sibling services' ``db.py`` modules.

Tables:
  - ``credibility``            current Beta-Bernoulli state per entity (edge or source), upserted.
  - ``credibility_history``    append-only audit row per entity per PredictionScored, with 95% CI.
  - ``processed_predictions``  idempotency guard: one row per applied ``prediction_id`` so an
                               at-least-once redelivery never double-counts alpha/beta.
"""

from __future__ import annotations

import asyncpg

SCHEMA_DDL = """
CREATE SCHEMA IF NOT EXISTS credibility;

-- Current Beta-Bernoulli state. One row per (entity_id, entity_type). entity_type is 'edge'
-- (Neo4j CAUSES edge, entity_id = 'FACTOR->ASSET') or 'source' (news domain, entity_id = domain).
CREATE TABLE IF NOT EXISTS credibility.credibility (
    entity_id          TEXT NOT NULL,
    entity_type        TEXT NOT NULL,
    alpha              DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    beta               DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    credibility_score  DOUBLE PRECISION NOT NULL,
    last_updated       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_id, entity_type)
);

-- Append-only audit trail: one row per entity per update event, never modified.
CREATE TABLE IF NOT EXISTS credibility.credibility_history (
    id                  BIGSERIAL PRIMARY KEY,
    entity_id           TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    prediction_id       UUID NOT NULL,
    alpha_before        DOUBLE PRECISION NOT NULL,
    beta_before         DOUBLE PRECISION NOT NULL,
    alpha_after         DOUBLE PRECISION NOT NULL,
    beta_after          DOUBLE PRECISION NOT NULL,
    credibility_before  DOUBLE PRECISION NOT NULL,
    credibility_after   DOUBLE PRECISION NOT NULL,
    ci_lower            DOUBLE PRECISION NOT NULL,
    ci_upper            DOUBLE PRECISION NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS credibility_history_entity_idx
    ON credibility.credibility_history (entity_id, entity_type, updated_at DESC);

CREATE INDEX IF NOT EXISTS credibility_history_prediction_idx
    ON credibility.credibility_history (prediction_id);

-- Idempotency guard. A PredictionScored is applied at most once; a redelivery (RabbitMQ is
-- at-least-once) finds the row already present and is skipped without touching any weights.
CREATE TABLE IF NOT EXISTS credibility.processed_predictions (
    prediction_id  UUID PRIMARY KEY,
    processed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Create an asyncpg connection pool for the credibility schema."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Apply the idempotent DDL that owns the ``credibility`` schema tables."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
