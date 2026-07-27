"""EventDetected outbox relay.

Relays PENDING `cleansing.outbox_events` rows to `feed.events` with routing key `event.detected`
via the shared RabbitMQ client, and reconciles rows left PENDING after a crash/restart. A publish
failure after the database commit is recovered here without creating a second event (functional
document sec 9, acceptance criterion 12).
"""

from __future__ import annotations

import uuid
from typing import Protocol

import asyncpg
from shared.schemas.messages import EventDetected


class SupportsPublish(Protocol):
    """Structural type for the shared RabbitMQ client's publish method."""

    async def publish(self, message: EventDetected) -> None: ...


class EventOutboxPublisher:
    """Relays pending event outbox rows to the broker and marks their delivery status."""

    def __init__(self, pool: asyncpg.Pool, publisher: SupportsPublish) -> None:
        self._pool = pool
        self._publisher = publisher

    async def publish_pending(self, *, batch_size: int = 100) -> int:
        """Publish up to `batch_size` pending outbox rows. Returns the count delivered."""
        rows = await self._pool.fetch(
            """
            SELECT message_id, payload
            FROM cleansing.outbox_events
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
                message = EventDetected.model_validate_json(payload)
                await self._publisher.publish(message)
            except Exception as exc:  # noqa: BLE001 - recorded per-row; other rows still proceed
                await self._pool.execute(
                    """
                    UPDATE cleansing.outbox_events
                    SET attempts = attempts + 1, last_error = $2
                    WHERE message_id = $1
                    """,
                    message_id,
                    str(exc),
                )
                continue

            await self._pool.execute(
                """
                UPDATE cleansing.outbox_events
                SET delivery_status = 'DELIVERED', delivered_at = now(), last_error = NULL
                WHERE message_id = $1
                """,
                message_id,
            )
            delivered += 1
        return delivered
