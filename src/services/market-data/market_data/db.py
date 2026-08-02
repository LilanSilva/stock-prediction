"""PostgreSQL access for the Market Data Service.

Owns the `market_data` schema application tables. Per the topology each service applies its own
table DDL; this module creates them idempotently at startup (the infra init script only creates the
schemas). The uniqueness rules prevent duplicate requests and duplicate provider observations from
producing duplicate outputs:

  - price_requests is keyed by request_id (deterministic work identity from the input message).
  - close_observations is unique per (asset_id, session, registry_version), so replaying a request
    reuses the stored observation rather than creating a second one.
  - outbox holds exactly one PriceObserved per request_id.
"""

from __future__ import annotations

import asyncpg

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS market_data.price_requests (
    request_id          UUID PRIMARY KEY,
    prediction_id       UUID NOT NULL,
    asset_id            TEXT NOT NULL,
    baseline_session    DATE NOT NULL,
    settlement_session  DATE NOT NULL,
    market_calendar     TEXT NOT NULL,
    correlation_id      UUID NOT NULL,
    state               TEXT NOT NULL DEFAULT 'PENDING',
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ,
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_price_requests_open
    ON market_data.price_requests (next_attempt_at)
    WHERE state IN ('PENDING', 'BASELINE_OBSERVED');

CREATE TABLE IF NOT EXISTS market_data.close_observations (
    id                  BIGSERIAL PRIMARY KEY,
    asset_id            TEXT NOT NULL,
    session             DATE NOT NULL,
    close               NUMERIC NOT NULL CHECK (close > 0),
    provider_bar_time   TIMESTAMPTZ,
    fetched_at          TIMESTAMPTZ NOT NULL,
    source              TEXT NOT NULL,
    provider_symbol     TEXT NOT NULL,
    price_kind          TEXT NOT NULL,
    is_adjusted         BOOLEAN NOT NULL,
    registry_version    TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, session, registry_version)
);

-- Supports the read-only recent-closes lookup (asset_id filter, session DESC ordering + LIMIT).
CREATE INDEX IF NOT EXISTS ix_close_observations_recent
    ON market_data.close_observations (asset_id, session DESC);

CREATE TABLE IF NOT EXISTS market_data.outbox (
    id              BIGSERIAL PRIMARY KEY,
    message_id      UUID NOT NULL UNIQUE,
    aggregate_id    UUID NOT NULL UNIQUE,
    routing_key     TEXT NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivery_status TEXT NOT NULL DEFAULT 'PENDING',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    delivered_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_market_data_outbox_pending
    ON market_data.outbox (created_at)
    WHERE delivery_status = 'PENDING';
"""


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Create an asyncpg connection pool for the market-data database."""
    return await asyncpg.create_pool(dsn=dsn, min_size=min_size, max_size=max_size)


async def apply_schema(pool: asyncpg.Pool) -> None:
    """Create the market_data tables/indexes idempotently."""
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_DDL)
