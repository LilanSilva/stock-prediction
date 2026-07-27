"""Transactional storage and outbox for the Ingestion Service.

`ArticleRepository.store_new_article` inserts the canonical article row and its outbox row in one
transaction, keyed by canonical URL for idempotency. `OutboxPublisher` relays pending outbox rows to
`feed.events` via the shared RabbitMQ client and reconciles rows left pending after a crash/restart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

import asyncpg
from shared.schemas.messages import ArticleIngested, RoutingKey

from ingestion.models import RawArticle
from ingestion.normalize import content_hash, normalize_text, truncate
from ingestion.urls import canonicalize_url


class SupportsPublish(Protocol):
    """Structural type for the shared RabbitMQ client's publish method."""

    async def publish(self, message: ArticleIngested) -> None: ...


class ArticleRepository:
    """Persists canonical articles with an accompanying outbox record."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def store_new_article(
        self,
        raw: RawArticle,
        body: str,
        *,
        correlation_id: uuid.UUID,
        max_body_chars: int = 2000,
    ) -> ArticleIngested | None:
        """Insert a new article + outbox row, or return None if the URL already exists.

        Returns the `ArticleIngested` message that was enqueued in the outbox, or None when the
        canonical URL is already present (idempotent no-op, no duplicate message).
        """
        canonical = canonicalize_url(str(raw.url))
        title = normalize_text(raw.title)
        clean_body = truncate(normalize_text(body), max_body_chars)
        digest = content_hash(title, clean_body)
        article_id = uuid.uuid4()

        message = ArticleIngested(
            message_id=uuid.uuid4(),
            correlation_id=correlation_id,
            occurred_at=datetime.now(UTC),
            article_id=article_id,
            source_id=raw.source_id,
            canonical_url=canonical,
            title=title,
            body=clean_body,
            published_at=raw.published_at,
            language=raw.language,
            country=raw.country,
            content_hash=digest,
        )
        payload = message.model_dump_json()

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    """
                    INSERT INTO ingestion.articles (
                        article_id, source_id, canonical_url, title, body,
                        published_at, language, country, content_hash
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (canonical_url) DO NOTHING
                    RETURNING article_id
                    """,
                    article_id,
                    raw.source_id,
                    canonical,
                    title,
                    clean_body,
                    raw.published_at,
                    raw.language,
                    raw.country,
                    digest,
                )
                if inserted is None:
                    return None
                await conn.execute(
                    """
                    INSERT INTO ingestion.outbox (message_id, aggregate_id, routing_key, payload)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    message.message_id,
                    article_id,
                    RoutingKey.ARTICLE_INGESTED.value,
                    payload,
                )
        return message


class OutboxPublisher:
    """Relays pending outbox rows to the broker and marks their delivery status."""

    def __init__(self, pool: asyncpg.Pool, publisher: SupportsPublish) -> None:
        self._pool = pool
        self._publisher = publisher

    async def publish_pending(self, *, batch_size: int = 100) -> int:
        """Publish up to `batch_size` pending outbox rows. Returns the count delivered."""
        rows = await self._pool.fetch(
            """
            SELECT message_id, payload
            FROM ingestion.outbox
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
                message = ArticleIngested.model_validate_json(payload)
                await self._publisher.publish(message)
            except Exception as exc:  # noqa: BLE001 - recorded per-row; other rows still proceed
                await self._pool.execute(
                    """
                    UPDATE ingestion.outbox
                    SET attempts = attempts + 1, last_error = $2
                    WHERE message_id = $1
                    """,
                    message_id,
                    str(exc),
                )
                continue

            await self._pool.execute(
                """
                UPDATE ingestion.outbox
                SET delivery_status = 'DELIVERED', delivered_at = now(), last_error = NULL
                WHERE message_id = $1
                """,
                message_id,
            )
            delivered += 1
        return delivered
