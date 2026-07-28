"""PostgreSQL repository for prediction contexts, predictions, and the outbox."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import asyncpg
from shared.schemas.messages import AssetId, EventType, PredictionMade

from prediction.models import ContextEvent, ContextRecord, ContextState

# States in which a new event joins the existing latest context version rather than starting a new
# one. A PREDICTED/PREDICTING/ERROR context is immutable to new members, so a late event supersedes.
_JOINABLE_STATES = (ContextState.OPEN.value, ContextState.READY.value)


class PredictionRepository:
    """Owns all reads/writes for the ``prediction`` schema."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def assign_event(
        self,
        *,
        asset_id: AssetId,
        event_id: uuid.UUID,
        event_type: EventType,
        first_seen_at: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> None:
        """Add a distinct event to the correct per-asset context version (idempotent membership).

        Joins the latest OPEN/READY version, or opens a new version (carrying prior members) when
        the latest version has already been predicted \u2014 the superseding late-event path.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                latest = await conn.fetchrow(
                    """
                    SELECT context_id, context_version, state
                    FROM prediction.contexts
                    WHERE asset_id = $1 AND window_start = $2
                    ORDER BY context_version DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    asset_id.value,
                    window_start,
                )

                if latest is None:
                    context_id = uuid.uuid4()
                    await conn.execute(
                        """
                        INSERT INTO prediction.contexts
                            (context_id, asset_id, window_start, window_end, context_version,
                             state, watermark)
                        VALUES ($1, $2, $3, $4, 1, 'OPEN', $5)
                        ON CONFLICT (asset_id, window_start, context_version) DO NOTHING
                        """,
                        context_id,
                        asset_id.value,
                        window_start,
                        window_end,
                        first_seen_at,
                    )
                elif latest["state"] in _JOINABLE_STATES:
                    context_id = latest["context_id"]
                    await conn.execute(
                        """
                        UPDATE prediction.contexts
                        SET watermark = GREATEST(watermark, $2), updated_at = now()
                        WHERE context_id = $1
                        """,
                        context_id,
                        first_seen_at,
                    )
                else:
                    context_id = uuid.uuid4()
                    new_version = int(latest["context_version"]) + 1
                    await conn.execute(
                        """
                        INSERT INTO prediction.contexts
                            (context_id, asset_id, window_start, window_end, context_version,
                             state, watermark)
                        VALUES ($1, $2, $3, $4, $5, 'OPEN', $6)
                        ON CONFLICT (asset_id, window_start, context_version) DO NOTHING
                        """,
                        context_id,
                        asset_id.value,
                        window_start,
                        window_end,
                        new_version,
                        first_seen_at,
                    )
                    # Carry prior members forward so the new version reflects the full window.
                    await conn.execute(
                        """
                        INSERT INTO prediction.context_events
                            (context_id, event_id, event_type, first_seen_at)
                        SELECT $1, event_id, event_type, first_seen_at
                        FROM prediction.context_events
                        WHERE context_id = $2
                        ON CONFLICT (context_id, event_id) DO NOTHING
                        """,
                        context_id,
                        latest["context_id"],
                    )

                await conn.execute(
                    """
                    INSERT INTO prediction.context_events
                        (context_id, event_id, event_type, first_seen_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (context_id, event_id) DO NOTHING
                    """,
                    context_id,
                    event_id,
                    event_type.value,
                    first_seen_at,
                )

    async def claim_ready_contexts(
        self, now: datetime, *, grace_minutes: int, limit: int = 20
    ) -> list[ContextRecord]:
        """Atomically move due OPEN/READY contexts to PREDICTING and return them."""
        cutoff = now - timedelta(minutes=grace_minutes)
        claimed: list[ContextRecord] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT context_id, asset_id, context_version, window_start, window_end
                    FROM prediction.contexts
                    WHERE state IN ('OPEN', 'READY') AND window_end <= $1
                    ORDER BY window_end
                    LIMIT $2
                    FOR UPDATE SKIP LOCKED
                    """,
                    cutoff,
                    limit,
                )
                for row in rows:
                    await conn.execute(
                        "UPDATE prediction.contexts SET state = 'PREDICTING', updated_at = now() "
                        "WHERE context_id = $1",
                        row["context_id"],
                    )
                    claimed.append(
                        ContextRecord(
                            context_id=row["context_id"],
                            asset_id=AssetId(row["asset_id"]),
                            context_version=row["context_version"],
                            window_start=row["window_start"],
                            window_end=row["window_end"],
                            state=ContextState.PREDICTING,
                        )
                    )
        return claimed

    async def load_context_events(self, context_id: uuid.UUID) -> list[ContextEvent]:
        rows = await self._pool.fetch(
            """
            SELECT event_id, event_type, first_seen_at
            FROM prediction.context_events
            WHERE context_id = $1
            ORDER BY first_seen_at
            """,
            context_id,
        )
        return [
            ContextEvent(
                event_id=row["event_id"],
                event_type=EventType(row["event_type"]),
                first_seen_at=row["first_seen_at"],
            )
            for row in rows
        ]

    async def latest_prediction_id(
        self, asset_id: AssetId, window_start: datetime
    ) -> uuid.UUID | None:
        """Most recent prediction for the same window, used to set ``supersedes_prediction_id``."""
        value = await self._pool.fetchval(
            """
            SELECT prediction_id
            FROM prediction.predictions
            WHERE asset_id = $1 AND context_id IN (
                SELECT context_id FROM prediction.contexts
                WHERE asset_id = $1 AND window_start = $2
            )
            ORDER BY context_version DESC
            LIMIT 1
            """,
            asset_id.value,
            window_start,
        )
        return value if value is None else uuid.UUID(str(value))

    async def set_context_state(self, context_id: uuid.UUID, state: ContextState) -> None:
        await self._pool.execute(
            "UPDATE prediction.contexts SET state = $2, updated_at = now() WHERE context_id = $1",
            context_id,
            state.value,
        )

    async def store_prediction_with_outbox(
        self, message: PredictionMade, *, idempotency_key: str
    ) -> bool:
        """Insert the prediction, its edges, and an outbox row in one transaction.

        Returns False when the idempotency key already exists (duplicate context version replay), in
        which case no second identity is created.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO prediction.predictions
                        (prediction_id, context_id, context_version, asset_id, direction, magnitude,
                         confidence, horizon, rationale, decision_method, decision_at,
                         supersedes_prediction_id, idempotency_key, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'PENDING')
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING prediction_id
                    """,
                    message.prediction_id,
                    message.context_id,
                    message.context_version,
                    message.asset_id.value,
                    message.direction.value,
                    message.magnitude.value,
                    message.confidence,
                    message.horizon.value,
                    message.rationale,
                    message.decision_method.value,
                    message.decision_at,
                    message.supersedes_prediction_id,
                    idempotency_key,
                )
                if inserted is None:
                    await conn.execute(
                        "UPDATE prediction.contexts SET state = 'PREDICTED', updated_at = now() "
                        "WHERE context_id = $1",
                        message.context_id,
                    )
                    return False

                for edge in message.contributing_edges:
                    await conn.execute(
                        """
                        INSERT INTO prediction.contributing_edges
                            (prediction_id, edge_id, direction, current_weight, influence_weight,
                             path)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        message.prediction_id,
                        edge.edge_id,
                        edge.direction.value,
                        edge.current_weight,
                        edge.influence_weight,
                        edge.path,
                    )

                await conn.execute(
                    """
                    INSERT INTO prediction.outbox_events (message_id, aggregate_id, payload)
                    VALUES ($1, $2, $3)
                    """,
                    message.message_id,
                    message.prediction_id,
                    message.model_dump_json(),
                )
                await conn.execute(
                    "UPDATE prediction.contexts SET state = 'PREDICTED', updated_at = now() "
                    "WHERE context_id = $1",
                    message.context_id,
                )
                return True
