"""Verification outbox relay.

Relays PENDING ``verification.outbox_events`` rows to ``feed.events`` via the shared RabbitMQ
client. Rows carry two message types — ``PriceRequested`` (``price.requested``) and
``PredictionScored`` (``prediction.scored``) — dispatched by the stored ``message_type``.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import asyncpg
from shared.schemas.messages import FeedMessage, PredictionScored, PriceRequested

_DECODERS = {
    "PriceRequested": PriceRequested.model_validate_json,
    "PredictionScored": PredictionScored.model_validate_json,
}


class SupportsPublish(Protocol):
    """Structural type for the shared RabbitMQ client's publish method."""

    async def publish(self, message: FeedMessage) -> None: ...


class VerificationOutboxPublisher:
    """Relays pending verification outbox rows to the broker and marks their delivery status."""

    def __init__(self, pool: asyncpg.Pool, publisher: SupportsPublish) -> None:
        self._pool = pool
        self._publisher = publisher

    async def publish_pending(self, *, batch_size: int = 100) -> int:
        """Publish up to ``batch_size`` pending outbox rows. Returns the count delivered."""
        rows = await self._pool.fetch(
            """
            SELECT message_id, message_type, payload
            FROM verification.outbox_events
            WHERE delivery_status = 'PENDING'
            ORDER BY created_at
            LIMIT $1
            """,
            batch_size,
        )

        delivered = 0
        for row in rows:
            message_id: uuid.UUID = row["message_id"]
            decoder = _DECODERS.get(row["message_type"])
            try:
                if decoder is None:
                    raise ValueError(f"unknown outbox message_type: {row['message_type']!r}")
                payload = row["payload"]
                message = decoder(payload if isinstance(payload, str) else str(payload))
                await self._publisher.publish(message)
            except Exception as exc:  # noqa: BLE001 - recorded per-row; other rows still proceed
                await self._pool.execute(
                    """
                    UPDATE verification.outbox_events
                    SET attempts = attempts + 1, last_error = $2
                    WHERE message_id = $1
                    """,
                    message_id,
                    str(exc),
                )
                continue

            await self._pool.execute(
                """
                UPDATE verification.outbox_events
                SET delivery_status = 'DELIVERED', delivered_at = now(), last_error = NULL
                WHERE message_id = $1
                """,
                message_id,
            )
            delivered += 1
        return delivered
