"""Transactional storage and outbox for the Market Data Service.

`PriceRequestRepository` persists the request row (idempotent on request_id) before the input
message is acknowledged, records immutable close observations, and enqueues exactly one
`PriceObserved` outbox row per request in the same transaction as the request-state transition.

`OutboxPublisher` relays pending `PriceObserved` rows to `feed.events` via the shared RabbitMQ
client and reconciles rows left pending after a crash/restart.

Request states:
  PENDING            -> request stored; no close observed yet.
  BASELINE_OBSERVED  -> baseline close stored; awaiting settlement completion.
  COMPLETED          -> both closes stored and the PriceObserved row enqueued.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

import asyncpg
import structlog
from shared.reference.loader import is_known_asset
from shared.schemas.messages import (
    AssetId,
    CloseObservation,
    PriceObserved,
    PriceRequested,
    RoutingKey,
)

logger = structlog.get_logger(__name__)

STATE_PENDING = "PENDING"
STATE_BASELINE_OBSERVED = "BASELINE_OBSERVED"
STATE_COMPLETED = "COMPLETED"
STATE_ABANDONED = "ABANDONED"

# States a scheduler tick may still act on; mirrors the ix_price_requests_open partial index.
OPEN_STATES = (STATE_PENDING, STATE_BASELINE_OBSERVED)

# Inclusive bounds for the recent-closes read query; caps the row count a single caller can pull.
MIN_RECENT_SESSIONS = 1
MAX_RECENT_SESSIONS = 250


async def get_recent_closes(
    pool: asyncpg.Pool, asset_id: AssetId, sessions: int
) -> list[tuple[date, Decimal]]:
    """Return the most recent (session, close) pairs for an asset, ordered by session DESC.

    `sessions` is clamped to [MIN_RECENT_SESSIONS, MAX_RECENT_SESSIONS] so a direct caller cannot
    request an unbounded scan; the canonical asset id and limit are passed as bound parameters.
    """
    bounded = max(MIN_RECENT_SESSIONS, min(sessions, MAX_RECENT_SESSIONS))
    rows = await pool.fetch(
        """
        SELECT session, close
        FROM market_data.close_observations
        WHERE asset_id = $1
        ORDER BY session DESC
        LIMIT $2
        """,
        str(asset_id),
        bounded,
    )
    return [(row["session"], row["close"]) for row in rows]



def observation_content_hash(asset_id: AssetId, observation: CloseObservation) -> str:
    """Stable content hash of an immutable observation, for auditable reproducibility."""
    parts = "|".join(
        [
            str(asset_id),
            observation.session.isoformat(),
            format(observation.close, "f"),
            observation.provider_symbol,
            observation.price_kind.value,
            str(observation.is_adjusted),
            observation.registry_version,
        ]
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PendingRequest:
    """A price request awaiting baseline and/or settlement observation."""

    request_id: uuid.UUID
    prediction_id: uuid.UUID
    asset_id: AssetId
    baseline_session: date
    settlement_session: date
    market_calendar: str
    correlation_id: uuid.UUID
    state: str
    attempts: int


class PriceRequestRepository:
    """Persists price requests, observations, and the PriceObserved outbox row."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def register_request(self, message: PriceRequested) -> bool:
        """Insert the request as PENDING, or return False if request_id already exists.

        Idempotent: duplicate delivery of the same request_id creates no second schedule.
        """
        inserted = await self._pool.fetchval(
            """
            INSERT INTO market_data.price_requests (
                request_id, prediction_id, asset_id, baseline_session,
                settlement_session, market_calendar, correlation_id, state, next_attempt_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
            ON CONFLICT (request_id) DO NOTHING
            RETURNING request_id
            """,
            message.request_id,
            message.prediction_id,
            str(message.asset_id),
            message.baseline_session,
            message.settlement_session,
            message.market_calendar,
            message.correlation_id,
            STATE_PENDING,
        )
        return inserted is not None

    async def load_open_requests(self) -> list[PendingRequest]:
        """Return requests due for another attempt, oldest first.

        Honours ``next_attempt_at`` so a deferred request is not re-driven before its backoff has
        elapsed, and skips terminal states.
        """
        rows = await self._pool.fetch(
            """
            SELECT request_id, prediction_id, asset_id, baseline_session,
                   settlement_session, market_calendar, correlation_id, state, attempts
            FROM market_data.price_requests
            WHERE state = ANY($1::text[])
              AND (next_attempt_at IS NULL OR next_attempt_at <= now())
            ORDER BY created_at
            """,
            list(OPEN_STATES),
        )
        open_requests: list[PendingRequest] = []
        for row in rows:
            # A request whose asset was retired from the registry can never be priced. Skipping it
            # keeps one stale row from raising and aborting the tick for every other request.
            if not is_known_asset(row["asset_id"]):
                logger.warning(
                    "open_request_asset_not_in_registry",
                    request_id=str(row["request_id"]),
                    asset_id=row["asset_id"],
                )
                continue
            open_requests.append(
                PendingRequest(
                    request_id=row["request_id"],
                    prediction_id=row["prediction_id"],
                    asset_id=AssetId(row["asset_id"]),
                    baseline_session=row["baseline_session"],
                    settlement_session=row["settlement_session"],
                    market_calendar=row["market_calendar"],
                    correlation_id=row["correlation_id"],
                    state=row["state"],
                    attempts=row["attempts"],
                )
            )
        return open_requests

    async def _upsert_observation(
        self, conn: asyncpg.Connection, asset_id: AssetId, observation: CloseObservation
    ) -> None:
        """Persist an immutable observation, ignoring a duplicate (asset, session, registry)."""
        await conn.execute(
            """
            INSERT INTO market_data.close_observations (
                asset_id, session, close, provider_bar_time, fetched_at, source,
                provider_symbol, price_kind, is_adjusted, registry_version, content_hash
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (asset_id, session, registry_version) DO NOTHING
            """,
            str(asset_id),
            observation.session,
            observation.close,
            observation.provider_bar_time,
            observation.fetched_at,
            observation.source,
            observation.provider_symbol,
            observation.price_kind.value,
            observation.is_adjusted,
            observation.registry_version,
            observation_content_hash(asset_id, observation),
        )

    async def mark_baseline_observed(
        self, request_id: uuid.UUID, asset_id: AssetId, baseline: CloseObservation
    ) -> None:
        """Store the baseline close and advance the request to BASELINE_OBSERVED."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._upsert_observation(conn, asset_id, baseline)
                await conn.execute(
                    """
                    UPDATE market_data.price_requests
                    SET state = $2, attempts = attempts + 1, updated_at = now(),
                        next_attempt_at = now(), last_error = NULL
                    WHERE request_id = $1 AND state = $3
                    """,
                    request_id,
                    STATE_BASELINE_OBSERVED,
                    STATE_PENDING,
                )

    async def complete_request(
        self,
        message: PriceObserved,
    ) -> bool:
        """Store both closes, enqueue the PriceObserved outbox row, and mark COMPLETED.

        All in one transaction so the state-change and publication intent are atomic. Idempotent:
        if the outbox row already exists (unique on request_id via aggregate_id) the request is
        already completed and False is returned.
        """
        payload = message.model_dump_json()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await self._upsert_observation(conn, message.asset_id, message.baseline)
                await self._upsert_observation(conn, message.asset_id, message.settlement)
                enqueued = await conn.fetchval(
                    """
                    INSERT INTO market_data.outbox (message_id, aggregate_id, routing_key, payload)
                    VALUES ($1, $2, $3, $4::jsonb)
                    ON CONFLICT (aggregate_id) DO NOTHING
                    RETURNING message_id
                    """,
                    message.message_id,
                    message.request_id,
                    RoutingKey.PRICE_OBSERVED.value,
                    payload,
                )
                if enqueued is None:
                    return False
                await conn.execute(
                    """
                    UPDATE market_data.price_requests
                    SET state = $2, attempts = attempts + 1, updated_at = now(), last_error = NULL
                    WHERE request_id = $1
                    """,
                    message.request_id,
                    STATE_COMPLETED,
                )
        return True

    async def record_failure(
        self, request_id: uuid.UUID, error: str, *, next_attempt_at: datetime | None
    ) -> None:
        """Record a transient/pending retry with a bounded next attempt time."""
        await self._pool.execute(
            """
            UPDATE market_data.price_requests
            SET attempts = attempts + 1, last_error = $2, next_attempt_at = $3, updated_at = now()
            WHERE request_id = $1
            """,
            request_id,
            error,
            next_attempt_at,
        )

    async def abandon_request(self, request_id: uuid.UUID, reason: str) -> None:
        """Move a request to the terminal ABANDONED state so it is never re-driven."""
        await self._pool.execute(
            """
            UPDATE market_data.price_requests
            SET state = $2, last_error = $3, next_attempt_at = NULL, updated_at = now()
            WHERE request_id = $1
            """,
            request_id,
            STATE_ABANDONED,
            reason,
        )


class SupportsPublish(Protocol):
    """Structural type for the shared RabbitMQ client's publish method."""

    async def publish(self, message: PriceObserved) -> None: ...


class OutboxPublisher:
    """Relays pending PriceObserved outbox rows to the broker and marks their delivery status."""

    def __init__(self, pool: asyncpg.Pool, publisher: SupportsPublish) -> None:
        self._pool = pool
        self._publisher = publisher

    async def publish_pending(self, *, batch_size: int = 100) -> int:
        """Publish up to `batch_size` pending outbox rows. Returns the count delivered."""
        rows = await self._pool.fetch(
            """
            SELECT message_id, payload
            FROM market_data.outbox
            WHERE delivery_status = 'PENDING'
            ORDER BY created_at
            LIMIT $1
            """,
            batch_size,
        )

        delivered = 0
        for row in rows:
            message_id: uuid.UUID = row["message_id"]
            raw_payload = row["payload"]
            payload = raw_payload if isinstance(raw_payload, str) else str(raw_payload)
            try:
                message = PriceObserved.model_validate_json(payload)
                await self._publisher.publish(message)
            except Exception as exc:  # noqa: BLE001 - recorded per-row; other rows still proceed
                await self._pool.execute(
                    """
                    UPDATE market_data.outbox
                    SET attempts = attempts + 1, last_error = $2
                    WHERE message_id = $1
                    """,
                    message_id,
                    str(exc),
                )
                continue

            await self._pool.execute(
                """
                UPDATE market_data.outbox
                SET delivery_status = 'DELIVERED', delivered_at = now(), last_error = NULL
                WHERE message_id = $1
                """,
                message_id,
            )
            delivered += 1
        return delivered


def build_price_observed(request: PendingRequest, baseline: CloseObservation,
                         settlement: CloseObservation) -> PriceObserved:
    """Assemble the single PriceObserved for a completed request (deterministic per request_id)."""
    return PriceObserved(
        message_id=uuid.uuid4(),
        correlation_id=request.correlation_id,
        causation_id=request.request_id,
        occurred_at=datetime.now(UTC),
        request_id=request.request_id,
        prediction_id=request.prediction_id,
        asset_id=request.asset_id,
        baseline=baseline,
        settlement=settlement,
    )
